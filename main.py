"""
main.py – Einstiegspunkt für den SBS Bildeditor v4.
Startet die Qt-Anwendung und öffnet das Hauptfenster.
"""
import sys, os

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon, QPalette, QColor

from sbs.editor import ImageEditor


def main():
    # Windows: Taskleiste zeigt App-Icon statt Python-Icon
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "julia.image_viewer.app")
    except Exception:
        pass

    app = QApplication(sys.argv)

    # App-Icon laden (absoluter Pfad, unabhängig vom Arbeitsverzeichnis)
    _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "app_icon.png")
    app.setWindowIcon(QIcon(_icon_path))
    app.setStyle("Fusion")

    # Dunkles Farbschema (Fusion Dark)
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
