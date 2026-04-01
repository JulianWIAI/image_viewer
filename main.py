"""
main.py – Entry point for the SBS Image Editor v4.
Initialises the Qt application, applies the dark colour scheme,
and opens the main editor window.
"""
import sys, os

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon, QPalette, QColor

from sbs.editor import ImageEditor


def main():
    # Windows: show the application icon in the taskbar instead of the generic Python icon
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "julia.image_viewer.app")
    except Exception:
        pass

    app = QApplication(sys.argv)

    # Load the app icon using an absolute path so it works regardless of the working directory
    _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "assets", "app_icon.png")
    app.setWindowIcon(QIcon(_icon_path))
    app.setStyle("Fusion")

    # Dark colour scheme (Fusion Dark palette)
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(38,  38,  38))
    p.setColor(QPalette.ColorRole.WindowText,      QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Base,            QColor(25,  25,  25))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(45,  45,  45))
    p.setColor(QPalette.ColorRole.Text,            QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Button,          QColor(55,  55,  55))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(14,  99, 156))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.ToolTipBase,     QColor(45,  45,  45))
    p.setColor(QPalette.ColorRole.ToolTipText,     QColor(220, 220, 220))
    app.setPalette(p)

    win = ImageEditor()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
