"""Test _on_frame QImage→numpy→evaluate path."""
import sys
sys.path.insert(0, '.')

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage

app = QApplication(sys.argv)

# Build a minimal RecipeTree with one PC-assigned tool
import numpy as np
from RecipeTree import RecipeTree
from HyVisionTools import HyAnd, HyLine

tree = RecipeTree()
logic = HyAnd(tool_id=10)
tree.add_tool(logic, parent_id=0)
# HyLine is a device-side physical tool; just test evaluate with empty PC tree
# (PC-assigned tools would be HyContrast, HyDistance, etc.)


# Fake a 320x240 RGB QImage
h, w = 240, 320
arr = np.full((h, w, 3), 128, dtype=np.uint8)
qimg = QImage(arr.data, w, h, w * 3, QImage.Format_RGB888).copy()

# Test the helper
from InspectorApp import InspectorApp
bgr = InspectorApp._qimage_to_numpy(qimg)
assert bgr.shape == (h, w, 3), f"shape wrong: {bgr.shape}"
assert bgr.dtype == np.uint8
print("1. _qimage_to_numpy OK, shape:", bgr.shape)

# Fake burst results (empty — no device tools in this tree)
from HyProtocol import HyProtocol
burst_results = []
tree.inject_burst(burst_results, cycle_id=1)
results = tree.evaluate(bgr, cycle_id=1, img_id=1)
print(f"2. evaluate returned {len(results)} result(s) OK")

app.quit()
print("ALL TESTS PASSED")
