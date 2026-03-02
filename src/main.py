import sys
import json
from typing import Any

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QLabel, QMessageBox,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QTextEdit
)
from PySide6.QtCore import Qt

from metadata import extract_metadata


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

        # GPS tab tree
        self.gps_tree = QTreeWidget()
        self.gps_tree.setHeaderLabels(["Field", "Value"])
        self.gps_tree.setColumnWidth(0, 450)
        self.tabs.addTab(self.gps_tree, "GPS")

        # Raw JSON tab
        self.raw_text = QTextEdit()
        self.raw_text.setReadOnly(True)
        self.raw_text.setFontFamily("Consolas")
        self.tabs.addTab(self.raw_text, "Raw JSON")

        main_layout.addLayout(btn_row)
        main_layout.addWidget(self.file_label)
        main_layout.addWidget(self.tabs)

        central.setLayout(main_layout)
        self.setCentralWidget(central)

    def open_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Image",
            "",
            "Images (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.bmp);;All Files (*.*)"
        )
        if not path:
            return

        try:
            self.data = extract_metadata(path)
            self.current_path = path
            self.file_label.setText(path)

            # Split views
            file_info = self.data.get("file", {})
            # Build a merged EXIF view for display convenience
            merged_exif = {}
            merged_exif.update(self.data.get("exif_pillow", {}) or {})
            for k, v in (self.data.get("exif_exifread", {}) or {}).items():
                merged_exif.setdefault(k, v)

            gps = self.data.get("gps") or {}

            populate_tree(self.file_tree, file_info)
            populate_tree(self.exif_tree, merged_exif)
            populate_tree(self.gps_tree, gps if gps else {"info": "No GPS metadata found"})

            self.raw_text.setText(json.dumps(self.data, indent=2, ensure_ascii=False))

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to extract metadata:\n{e}")

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