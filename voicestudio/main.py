import sys
from PyQt6.QtWidgets import QApplication
from voicestudio.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("SQ5 Voice Studio")

    # Dark palette
    from PyQt6.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor("#1e1e2e"))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor("#cdd6f4"))
    palette.setColor(QPalette.ColorRole.Base,            QColor("#181825"))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor("#1e1e2e"))
    palette.setColor(QPalette.ColorRole.Text,            QColor("#cdd6f4"))
    palette.setColor(QPalette.ColorRole.Button,          QColor("#313244"))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor("#cdd6f4"))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor("#89b4fa"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#1e1e2e"))
    app.setPalette(palette)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
