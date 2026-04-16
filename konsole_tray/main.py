import signal
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from konsole_tray.tray import KonsoleTray


def main() -> None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Konsole Tab Finder")

    # Allow Ctrl-C to work: Python signal handlers only run between Qt events,
    # so we need a timer to give Python a chance to process them.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(200)

    tray = KonsoleTray()
    tray.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
