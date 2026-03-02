import sys
import json
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QPushButton, QFileDialog, QTextEdit, QLabel, QMessageBox,
    QHBoxLayout
)
from PySide6.QtCore import Qt

from metadata import extract_metadata


class MetadataViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Image Metadata Viewer")
        self.resize(1000, 700)
        self.data = None
        self.init_ui()

    def init_ui(self):
        central = QWidget()
        layout = QVBoxLayout()

        btn_row = QHBoxLayout()
        open_btn = QPushButton("Open Image")
        open_btn.clicked.connect(self.open_image)

        save_btn = QPushButton("Save JSON")
        save_btn.clicked.connect(self.save_json)

        copy_btn = QPushButton("Copy JSON")
        copy_btn.clicked.connect(self.copy_json)

        btn_row.addWidget(open_btn)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(copy_btn)

        self.file_label = QLabel("No file selected")
        self.file_label.setAlignment(Qt.AlignLeft)

        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setFontFamily("Consolas")

        layout.addLayout(btn_row)
        layout.addWidget(self.file_label)
        layout.addWidget(self.text_area)

        central.setLayout(layout)
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
            pretty = json.dumps(self.data, indent=2, ensure_ascii=False)
            self.file_label.setText(path)
            self.text_area.setText(pretty)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to extract metadata:\n{e}")

    def save_json(self):
        if not self.data:
            QMessageBox.information(self, "Info", "Open an image first.")
            return
        save_path, _ = QFileDialog.getSaveFileName(self, "Save JSON", "", "JSON Files (*.json)")
        if save_path:
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)

    def copy_json(self):
        if not self.data:
            QMessageBox.information(self, "Info", "Open an image first.")
            return
        QApplication.clipboard().setText(json.dumps(self.data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # looks nicer than default

    window = MetadataViewer()
    window.show()
    sys.exit(app.exec())
