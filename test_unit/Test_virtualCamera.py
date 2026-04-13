import sys
import os

from PyQt5.QtWidgets import QApplication, QMainWindow, QLabel, QVBoxLayout, QWidget, QPushButton

from PyQt5.QtGui import QPixmap

from PyQt5.QtCore import Qt

from OpenMVWorker import OpenMVWorker


class TestVirtualCamera(QMainWindow):

    def __init__(self):

        super().__init__()

        self.setWindowTitle("QA Test: Virtual Camera Loader")

        self.resize(680, 560)
        

        # 더미 이미지 폴더 생성 (테스트용)

        self.test_folder = "test_images"

        os.makedirs(self.test_folder, exist_ok=True)


        # UI 세팅

        main_widget = QWidget()

        self.setCentralWidget(main_widget)

        layout = QVBoxLayout(main_widget)
        

        self.lbl_image = QLabel("Waiting for Virtual Camera...")

        self.lbl_image.setAlignment(Qt.AlignCenter)

        self.lbl_image.setStyleSheet("background-color: black; color: white; font-size: 16px;")

        self.lbl_image.setFixedSize(640, 480)

        layout.addWidget(self.lbl_image)
        

        self.lbl_log = QLabel("Log: ")

        layout.addWidget(self.lbl_log)
        

        self.btn_start = QPushButton("가상 카메라 시작 (100ms 간격)")

        self.btn_start.clicked.connect(self.start_test)

        layout.addWidget(self.btn_start)
        

        self._create_dummy_image()


        # 워커 인스턴스

        self.worker = OpenMVWorker()

        self.worker.log_signal.connect(self.on_log)

        self.worker.frame_signal.connect(self.on_frame)
        

    def _create_dummy_image(self):

        # 테스트를 위해 빈 텍스트 파일(이미지로 위장) 생성 방지용 실제 이미지 생성 생략

        # 사용자가 test_images 폴더에 아무 jpg 파일이나 넣고 테스트하시면 됩니다.
        self.lbl_log.setText(f"안내: '{self.test_folder}' 폴더에 .jpg 이미지 파일들을 넣어주세요.")


    def start_test(self):

        self.btn_start.setEnabled(False)

        self.lbl_log.setText("가상 카메라 워커 시작됨...")

        # 폴더 내 이미지를 100ms(10FPS) 간격으로 읽어옴

        self.worker.start_virtual_camera(self.test_folder, interval_ms=100)
        

    def on_frame(self, qimg, img_id):

        # 워커에서 보낸 이미지를 받아서 라벨에 표시

        pixmap = QPixmap.fromImage(qimg).scaled(640, 480, Qt.KeepAspectRatio)

        self.lbl_image.setPixmap(pixmap)

        self.lbl_log.setText(f"프레임 수신 완료! imgID: {img_id}")
        

    def on_log(self, msg, mtype):

        print(f"[{mtype.upper()}] {msg}")

        if mtype == "error":

            self.lbl_log.setText(f"에러: {msg}")


    def closeEvent(self, event):

        self.worker.stop()

        event.accept()


if __name__ == "__main__":

    app = QApplication(sys.argv)

    window = TestVirtualCamera()

    window.show()

    sys.exit(app.exec_())