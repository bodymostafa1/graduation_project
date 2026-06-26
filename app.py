"""
Entry point — PyQt6 application bootstrap.
"""
import sys
from PyQt6.QtWidgets import QApplication
from state import app_state
from data import load_datasets
from styles import DARK_QSS
from ui import MainWindow


def main():
    load_datasets()

    qt_app = QApplication(sys.argv)
    qt_app.setStyleSheet(DARK_QSS)

    window = MainWindow()
    window.show()

    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()