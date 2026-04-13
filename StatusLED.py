from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QColor, QBrush, QRadialGradient

class StatusLED(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.state = 0 
    def set_state(self, state):
        self.state = state; self.update()
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor("#475569") 
        if self.state == 1: color = QColor("#10b981") 
        elif self.state == 2: color = QColor("#ef4444") 
        gradient = QRadialGradient(8, 8, 8, 8, 8)
        gradient.setColorAt(0, color.lighter(150)); gradient.setColorAt(0.5, color); gradient.setColorAt(1, color.darker(200))
        painter.setBrush(QBrush(gradient)); painter.setPen(Qt.NoPen); painter.drawEllipse(0, 0, 16, 16)
        painter.setBrush(QBrush(QColor(255, 255, 255, 100))); painter.drawEllipse(3, 2, 6, 4)