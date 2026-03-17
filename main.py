import sys
import torch  # must be imported before PyQt6 on Windows to avoid DLL path conflicts
from PyQt6.QtWidgets import QApplication
from src.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Image Manager")
    app.setOrganizationName("ImageManager")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
