"""Independent project identity; legacy technical interfaces stay compatible."""
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QLabel
from .project_identity import NAME, ICON


def apply(window):
    window.setWindowTitle(NAME)
    QApplication.setApplicationDisplayName(NAME)
    icon = QPixmap()
    icon.loadFromData(ICON)
    window.setWindowIcon(QIcon(icon))
    for label in window.findChildren(QLabel):
        if label.text() == 'ATRACSYS TRACKING':
            label.setText('MOTION TRACKING')
            label.setToolTip('Independent project. Not affiliated with or endorsed by the hardware manufacturer.')
