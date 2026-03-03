import sys
import json
from typing import Any
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QLabel, QMessageBox,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QTextEdit,
    QListWidget, QListWidgetItem, QCheckBox, QTableWidget,
    QTableWidgetItem
)
from PySide6.QtCore import Qt, QUrl
from metadata import extract_metadata, sanitize_image
from PySide6.QtGui import QDesktopServices, QPixmap
import os
import csv


def add_to_tree(parent: QTreeWidgetItem, key: str, value: Any) -> None:
    """
    Recursively add dict/list structures into a QTreeWidget.
    Leaf nodes show a value in column 2.
    """
    item = QTreeWidgetItem([str(key), ""])
    parent.addChild(item)

    if isinstance(value, dict):
        # Add children sorted by key for consistency
        for k in sorted(value.keys(), key=lambda x: str(x).lower()):
            add_to_tree(item, str(k), value[k])
    elif isinstance(value, list):
        for i, v in enumerate(value):
            add_to_tree(item, f"[{i}]", v)
    else:
        item.setText(1, str(value))


def populate_tree(tree: QTreeWidget, data: dict) -> None:
    tree.clear()
    root = tree.invisibleRootItem()
    for k in sorted(data.keys(), key=lambda x: str(x).lower()):
        add_to_tree(root, str(k), data[k])
    tree.expandToDepth(1)


class MetadataViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Image Metadata Viewer")
        self.resize(1100, 750)

        self.data = None
        self.current_path = None
        self.setAcceptDrops(True)        

        self.init_ui()

    def init_ui(self):
        central = QWidget()
        main_layout = QVBoxLayout()

        # --- Top controls ---
        btn_row = QHBoxLayout()

        self.open_btn = QPushButton("Open Image")
        self.open_btn.clicked.connect(self.open_image)

        self.copy_btn = QPushButton("Copy Current Tab")
        self.copy_btn.clicked.connect(self.copy_current_tab)

        self.save_btn = QPushButton("Save Raw JSON")
        self.save_btn.clicked.connect(self.save_json)

        btn_row.addWidget(self.open_btn)
        btn_row.addWidget(self.copy_btn)
        btn_row.addWidget(self.save_btn)

        self.file_label = QLabel("No file selected")
        self.file_label.setAlignment(Qt.AlignLeft)

        # --- Tabs ---
        self.tabs = QTabWidget()

        # File tab tree
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["Field", "Value"])
        self.file_tree.setColumnWidth(0, 350)
        self.tabs.addTab(self.file_tree, "File")

        # EXIF tab tree (merged view)
        self.exif_tree = QTreeWidget()
        self.exif_tree.setHeaderLabels(["Tag", "Value"])
        self.exif_tree.setColumnWidth(0, 450)
        self.tabs.addTab(self.exif_tree, "EXIF")

        # GPS tab (panel: coords + open button + tree)
        self.gps_tab = QWidget()
        gps_layout = QVBoxLayout()

        self.gps_coords_label = QLabel("GPS: (none)")
        self.gps_coords_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.open_maps_btn = QPushButton("Open in Maps")
        self.open_maps_btn.setEnabled(False)
        self.open_maps_btn.clicked.connect(self.open_in_maps)

        self.map_view = QWebEngineView()
        self.map_view.setMinimumHeight(260)
        gps_layout.addWidget(self.map_view)

        self.gps_tree = QTreeWidget()
        self.gps_tree.setHeaderLabels(["Field", "Value"])
        self.gps_tree.setColumnWidth(0, 450)

        gps_layout.addWidget(self.gps_coords_label)
        gps_layout.addWidget(self.open_maps_btn)
        gps_layout.addWidget(self.gps_tree)

        self.gps_tab.setLayout(gps_layout)
        self.tabs.addTab(self.gps_tab, "GPS")

        # Raw JSON tab
        self.raw_text = QTextEdit()
        self.raw_text.setReadOnly(True)
        self.raw_text.setFontFamily("Consolas")
        self.tabs.addTab(self.raw_text, "Raw JSON")

        # Privacy tab
        self.privacy_tab = QWidget()
        privacy_layout = QVBoxLayout()

        self.risk_label = QLabel("Privacy risk: (none)")
        self.risk_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.findings_list = QListWidget()
        self.recs_list = QListWidget()

        # --- Sanitization controls ---
        privacy_layout.addWidget(QLabel("Sanitize options (save a cleaned copy):"))

        self.cb_remove_all = QCheckBox("Remove ALL metadata")
        self.cb_remove_gps = QCheckBox("Remove GPS only")
        self.cb_remove_timestamps = QCheckBox("Remove timestamps")
        self.cb_remove_device = QCheckBox("Remove device identifiers (serials)")
        self.cb_remove_identity = QCheckBox("Remove identity fields (artist/owner/comment)")
        self.cb_keep_orientation = QCheckBox("Keep orientation (recommended)")
        self.cb_keep_orientation.setChecked(True)

        privacy_layout.addWidget(self.cb_remove_all)
        privacy_layout.addWidget(self.cb_remove_gps)
        privacy_layout.addWidget(self.cb_remove_timestamps)
        privacy_layout.addWidget(self.cb_remove_device)
        privacy_layout.addWidget(self.cb_remove_identity)
        privacy_layout.addWidget(self.cb_keep_orientation)

        self.btn_save_sanitized = QPushButton("Save Sanitized Copy…")
        self.btn_save_sanitized.clicked.connect(self.save_sanitized_copy)
        self.btn_save_sanitized.setEnabled(False)

        privacy_layout.addWidget(self.btn_save_sanitized)

        privacy_layout.addWidget(self.risk_label)
        privacy_layout.addWidget(QLabel("Findings:"))
        privacy_layout.addWidget(self.findings_list)
        privacy_layout.addWidget(QLabel("Recommendations:"))
        privacy_layout.addWidget(self.recs_list)

        self.privacy_tab.setLayout(privacy_layout)
        self.tabs.addTab(self.privacy_tab, "Privacy")

        # --- Batch tab ---
        self.batch_tab = QWidget()
        batch_layout = QVBoxLayout()

        batch_btns = QHBoxLayout()
        self.btn_pick_folder = QPushButton("Select Folder…")
        self.btn_pick_folder.clicked.connect(self.pick_folder)

        self.btn_scan_folder = QPushButton("Scan Folder")
        self.btn_scan_folder.clicked.connect(self.scan_folder)
        self.btn_scan_folder.setEnabled(False)

        self.btn_export_csv = QPushButton("Export CSV…")
        self.btn_export_csv.clicked.connect(self.export_csv)
        self.btn_export_csv.setEnabled(False)

        batch_btns.addWidget(self.btn_pick_folder)
        batch_btns.addWidget(self.btn_scan_folder)
        batch_btns.addWidget(self.btn_export_csv)

        self.batch_folder_label = QLabel("Folder: (none)")
        self.batch_folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.batch_table = QTableWidget()
        self.batch_table.setColumnCount(9)
        self.batch_table.setHorizontalHeaderLabels([
            "File",
            "Size (bytes)",
            "Has EXIF",
            "Has GPS",
            "Latitude",
            "Longitude",
            "Risk Level",
            "Risk Score",
            "Camera Model",
        ])
        self.batch_table.setSortingEnabled(True)
        self.batch_table.itemSelectionChanged.connect(self.on_batch_selection_changed)

        batch_layout.addLayout(batch_btns)
        batch_layout.addWidget(self.batch_folder_label)
        batch_layout.addWidget(self.batch_table)

        self.batch_tab.setLayout(batch_layout)
        self.tabs.addTab(self.batch_tab, "Batch")

        # storage for scan results
        self.batch_results = []
        self.batch_folder = None

        # --- Preview + Tabs (side-by-side) ---
        content_row = QHBoxLayout()

        # Left: Image preview panel
        preview_col = QVBoxLayout()
        self.preview_label = QLabel("No image loaded")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumWidth(320)
        self.preview_label.setMinimumHeight(320)
        self.preview_label.setStyleSheet("border: 1px solid #444; padding: 6px;")
        self.preview_label.setScaledContents(False)  # we scale manually for quality
        self.preview_label.setAcceptDrops(True)

        preview_col.addWidget(self.preview_label)

        # Right: Tabs
        content_row.addLayout(preview_col, 1)
        content_row.addWidget(self.tabs, 3)

        main_layout.addLayout(btn_row)
        main_layout.addWidget(self.file_label)
        main_layout.addLayout(content_row)

        central.setLayout(main_layout)
        self.setCentralWidget(central)

    def _set_preview(self, path: str) -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.preview_label.setText("Preview unavailable")
            self.preview_label.setPixmap(QPixmap())
            return

        # Fit the image to the label while keeping aspect ratio
        target_size = self.preview_label.size()
        scaled = pixmap.scaled(
            target_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        self.preview_label.setPixmap(scaled)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            # Accept if any URL looks like a local file
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    event.acceptProposedAction()
                    return
        event.ignore()

    def on_batch_selection_changed(self):
        row = self.batch_table.currentRow()
        if row < 0:
            return

        item = self.batch_table.item(row, 0)
        if not item:
            return

        path = item.data(Qt.UserRole)
        if not path:
            return

        if hasattr(self, "_set_preview"):
            self._set_preview(path)

        self.file_label.setText(path)

    def dropEvent(self, event):
        if not event.mimeData().hasUrls():
            event.ignore()
            return

        # Take the first local file dropped
        local_files = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if not local_files:
            event.ignore()
            return

        path = local_files[0]

        # Basic extension filter (fast + simple)
        allowed = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".heic")
        if not path.lower().endswith(allowed):
            QMessageBox.information(self, "Not an image", "Please drop an image file.")
            return

        self.load_image(path)
        event.acceptProposedAction()

    def load_image(self, path: str) -> None:
        try:
            self.data = extract_metadata(path)
            self.current_path = path
            if hasattr(self, "btn_save_sanitized"):
                self.btn_save_sanitized.setEnabled(True)
            self.file_label.setText(path)

            # Preview (if you added the preview feature)
            if hasattr(self, "_set_preview"):
                self._set_preview(path)

            # Split views
            file_info = self.data.get("file", {})
            merged_exif = {}
            merged_exif.update(self.data.get("exif_pillow", {}) or {})
            for k, v in (self.data.get("exif_exifread", {}) or {}).items():
                merged_exif.setdefault(k, v)

            gps = self.data.get("gps") or {}
            gps_dec = self.data.get("gps_decimal")

            populate_tree(self.file_tree, file_info)
            populate_tree(self.exif_tree, merged_exif)
            populate_tree(self.gps_tree, gps if gps else {"info": "No GPS metadata found"})

            # GPS decimal label + maps button (if present in your UI)
            if hasattr(self, "gps_coords_label") and hasattr(self, "open_maps_btn"):
                if gps_dec and "latitude" in gps_dec and "longitude" in gps_dec:
                    self.gps_coords_label.setText(
                        f"GPS: {gps_dec['latitude']:.6f}, {gps_dec['longitude']:.6f}"
                    )
                    self.open_maps_btn.setEnabled(True)
                else:
                    self.gps_coords_label.setText("GPS: (none)")
                    self.open_maps_btn.setEnabled(False)

            if hasattr(self, "map_view"):
                if gps_dec and gps_dec.get("osm_embed_url"):
                    self.map_view.setUrl(QUrl(gps_dec["osm_embed_url"]))
                else:
                    # Blank map when no GPS
                    self.map_view.setHtml("<html><body><h3>No GPS data to display.</h3></body></html>")

            self.raw_text.setText(json.dumps(self.data, indent=2, ensure_ascii=False))
            
            privacy = self.data.get("privacy") or {}
            level = privacy.get("level", "UNKNOWN")
            score = privacy.get("score", 0)
            self.risk_label.setText(f"Privacy risk: {level} (score {score})")

            self.findings_list.clear()
            for f in privacy.get("findings", []):
                item = QListWidgetItem(f"[{f.get('severity')}] {f.get('message')}")
                self.findings_list.addItem(item)

            self.recs_list.clear()
            for r in privacy.get("recommendations", []):
                self.recs_list.addItem(QListWidgetItem(f"• {r}"))

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to extract metadata:\n{e}")

    def open_in_maps(self):
        if not self.data:
            return
        gps_dec = self.data.get("gps_decimal") or {}
        url = gps_dec.get("maps_url")
        if not url:
            QMessageBox.information(self, "Info", "No GPS coordinates available.")
            return
        QDesktopServices.openUrl(QUrl(url))

    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select a folder")
        if not folder:
            return
        self.batch_folder = folder
        self.batch_folder_label.setText(f"Folder: {folder}")
        self.btn_scan_folder.setEnabled(True)

    def scan_folder(self):
        if not self.batch_folder:
            QMessageBox.information(self, "Info", "Select a folder first.")
            return

        allowed = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".heic")
        files = []
        for name in os.listdir(self.batch_folder):
            path = os.path.join(self.batch_folder, name)
            if os.path.isfile(path) and name.lower().endswith(allowed):
                files.append(path)

        if not files:
            QMessageBox.information(self, "Info", "No images found in that folder.")
            return

        self.batch_results = []
        self.batch_table.setRowCount(0)

        for path in files:
            try:
                data = extract_metadata(path, compute_hashes=False)

                file_info = data.get("file", {})
                merged_exif = {}
                merged_exif.update(data.get("exif_pillow", {}) or {})
                for k, v in (data.get("exif_exifread", {}) or {}).items():
                    merged_exif.setdefault(k, v)

                gps_dec = data.get("gps_decimal") or {}
                has_exif = bool(merged_exif)
                has_gps = bool(data.get("gps")) or ("latitude" in gps_dec and "longitude" in gps_dec)

                privacy = data.get("privacy") or {}
                risk_level = privacy.get("level", "UNKNOWN")
                risk_score = privacy.get("score", 0)

                # Camera model varies by library; common keys:
                camera_model = (
                    merged_exif.get("Model")
                    or merged_exif.get("Image Model")
                    or merged_exif.get("EXIF LensModel")
                    or ""
                )

                row = {
                    "path": path,
                    "file": os.path.basename(path),
                    "size_bytes": file_info.get("size_bytes", 0),
                    "has_exif": has_exif,
                    "has_gps": has_gps,
                    "lat": gps_dec.get("latitude", ""),
                    "lon": gps_dec.get("longitude", ""),
                    "risk_level": risk_level,
                    "risk_score": risk_score,
                    "camera_model": str(camera_model),
                }

                self.batch_results.append(row)

            except Exception:
                # Keep going; add a row showing failure
                self.batch_results.append({
                    "path": path,
                    "file": os.path.basename(path),
                    "size_bytes": os.path.getsize(path),
                    "has_exif": False,
                    "has_gps": False,
                    "lat": "",
                    "lon": "",
                    "risk_level": "ERROR",
                    "risk_score": 0,
                    "camera_model": "",
                })

        self._render_batch_table()
        self.btn_export_csv.setEnabled(True)

    def _render_batch_table(self):
        self.batch_table.setRowCount(len(self.batch_results))

        for r, row in enumerate(self.batch_results):
            vals = [
                row["file"],
                str(row["size_bytes"]),
                "Yes" if row["has_exif"] else "No",
                "Yes" if row["has_gps"] else "No",
                f"{row['lat']:.6f}" if isinstance(row["lat"], (float, int)) else str(row["lat"]),
                f"{row['lon']:.6f}" if isinstance(row["lon"], (float, int)) else str(row["lon"]),
                row["risk_level"],
                str(row["risk_score"]),
                row["camera_model"],
            ]

            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)

                # Store full file path in first column (sorting-safe)
                if c == 0:
                    item.setToolTip(row["path"])
                    item.setData(Qt.UserRole, row["path"])  # 🔥 important

                self.batch_table.setItem(r, c, item)

        self.batch_table.resizeColumnsToContents()

    def export_csv(self):
        if not self.batch_results:
            QMessageBox.information(self, "Info", "Run a scan first.")
            return

        out_path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "", "CSV Files (*.csv)")
        if not out_path:
            return

        fieldnames = [
            "path", "file", "size_bytes",
            "has_exif", "has_gps",
            "lat", "lon",
            "risk_level", "risk_score",
            "camera_model",
        ]

        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.batch_results)

        QMessageBox.information(self, "Exported", f"CSV saved:\n{out_path}")

    def open_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Image",
            "",
            "Images (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.bmp);;All Files (*.*)"
        )
        if not path:
            return
        self.load_image(path)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.current_path:
            self._set_preview(self.current_path)

    def save_sanitized_copy(self):
        if not self.current_path:
            QMessageBox.information(self, "Info", "Open an image first.")
            return

        # Choose output path
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Sanitized Copy",
            "",
            "Images (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.bmp);;All Files (*.*)"
        )
        if not out_path:
            return

        report = sanitize_image(
            self.current_path,
            out_path,
            remove_all=self.cb_remove_all.isChecked(),
            remove_gps=self.cb_remove_gps.isChecked(),
            remove_timestamps=self.cb_remove_timestamps.isChecked(),
            remove_device_ids=self.cb_remove_device.isChecked(),
            remove_identity=self.cb_remove_identity.isChecked(),
            keep_orientation=self.cb_keep_orientation.isChecked(),
        )

        QMessageBox.information(
            self,
            "Saved",
            "Sanitized copy saved.\n\nNotes:\n- " + "\n- ".join(report.get("notes", []))
        )

    def save_json(self):
        if not self.data:
            QMessageBox.information(self, "Info", "Open an image first.")
            return

        save_path, _ = QFileDialog.getSaveFileName(self, "Save Raw JSON", "", "JSON Files (*.json)")
        if not save_path:
            return

        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def copy_current_tab(self):
        if not self.data:
            QMessageBox.information(self, "Info", "Open an image first.")
            return

        idx = self.tabs.currentIndex()
        tab_name = self.tabs.tabText(idx)

        # Copy raw JSON if on raw tab; otherwise copy visible tree as a text report
        if tab_name == "Raw JSON":
            text = self.raw_text.toPlainText()
        else:
            tree = None
            if tab_name == "File":
                tree = self.file_tree
            elif tab_name == "EXIF":
                tree = self.exif_tree
            elif tab_name == "GPS":
                tree = self.gps_tree

            text = self._tree_to_text(tree) if tree else ""

        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "Copied", f"Copied: {tab_name}")

    def _tree_to_text(self, tree: QTreeWidget) -> str:
        """
        Convert the tree contents to a simple indented text block for copy/paste.
        """
        lines = []

        def walk(item: QTreeWidgetItem, depth: int):
            key = item.text(0)
            val = item.text(1)
            indent = "  " * depth
            if val:
                lines.append(f"{indent}{key}: {val}")
            else:
                lines.append(f"{indent}{key}")
            for i in range(item.childCount()):
                walk(item.child(i), depth + 1)

        root = tree.invisibleRootItem()
        for i in range(root.childCount()):
            walk(root.child(i), 0)

        return "\n".join(lines)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # nicer default styling

    window = MetadataViewer()
    window.show()
    sys.exit(app.exec())