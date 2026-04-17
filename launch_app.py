"""Launch InspectorApp with VVM pre-connected to test_images folder."""
import sys
sys.path.insert(0, '.')

from PyQt5.QtWidgets import QApplication
from InspectorApp import InspectorApp

app = QApplication(sys.argv)
w = InspectorApp()
w.show()
sys.exit(app.exec_())
