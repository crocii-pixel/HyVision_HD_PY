"""End-to-end pipeline smoke test: VVM → HyLink → _on_frame."""
import sys
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt, QTimer

app = QApplication(sys.argv)

frames_received = []

def on_frame(qimg, burst):
    frames_received.append((qimg.width(), qimg.height(), len(burst)))

TEST_IMG_DIR = 'C:/Users/MSI/OneDrive/Documents/Dev/UI/HyVision_HD_PY/test_images'
from VirtualMachine import VirtualMachine, FolderProvider
from HyLink import HyLink
from HyProtocol import HyProtocol

provider = FolderProvider(TEST_IMG_DIR)
vm = VirtualMachine(provider)
link = HyLink()
link.sig_frame.connect(on_frame, Qt.DirectConnection)
link.connect_virtual(vm)
link.send_command(HyProtocol.CMD_LIVE)

def check():
    print(f'frames: {len(frames_received)}')
    if frames_received:
        print(f'  last frame size: {frames_received[-1][0]}x{frames_received[-1][1]}, burst_len={frames_received[-1][2]}')
    link.disconnect_device()
    app.quit()

QTimer.singleShot(2500, check)
sys.exit(app.exec_())
