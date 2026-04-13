import sys
import serial.tools.list_ports
from PyQt5.QtWidgets import (QApplication, QMainWindow, QLabel, QVBoxLayout, 
                             QWidget, QPushButton, QComboBox, QHBoxLayout, QTextEdit)
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt, QTimer
from OpenMVWorker import OpenMVWorker

class TestSerialWorker(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QA Test: 실장비 Serial Burst 수신 테스트")
        self.resize(800, 600)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        
        # 1. 포트 선택부
        hbox = QHBoxLayout()
        self.cmb_ports = QComboBox()
        self.refresh_ports()
        hbox.addWidget(self.cmb_ports)
        
        self.btn_connect = QPushButton("장치 연결 및 수신 시작")
        self.btn_connect.clicked.connect(self.toggle_connection)
        hbox.addWidget(self.btn_connect)
        layout.addLayout(hbox)

        # 2. 이미지 표시부
        self.lbl_image = QLabel("Waiting for Camera...")
        self.lbl_image.setAlignment(Qt.AlignCenter)
        self.lbl_image.setStyleSheet("background-color: #0f172a; color: #94a3b8;")
        self.lbl_image.setFixedSize(640, 480)
        layout.addWidget(self.lbl_image, alignment=Qt.AlignCenter)
        
        # 3. 로그 및 패킷 덤프 표시부
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet("background-color: #000; color: #10b981; font-family: Consolas;")
        self.txt_log.setMaximumHeight(150)
        layout.addWidget(self.txt_log)
        
        self.worker = None

        # 주기적으로 트리거를 전송하기 위한 타이머
        self.trigger_timer = QTimer(self)
        self.trigger_timer.timeout.connect(self.send_test_trigger)

    def refresh_ports(self):
        self.cmb_ports.clear()
        for p in serial.tools.list_ports.comports():
            self.cmb_ports.addItem(f"{p.device} - {p.description}", p.device)

    def toggle_connection(self):
        if self.worker is None or not self.worker.running:
            if self.cmb_ports.count() == 0: return
            
            port_name = self.cmb_ports.currentData()
            self.log_msg(f"[{port_name}] 연결 시도 중...", "system")
            
            # 워커 생성 시 포트 이름을 넘겨줌
            self.worker = OpenMVWorker(port_name=port_name)
            self.worker.log_signal.connect(self.log_msg)
            self.worker.frame_signal.connect(self.on_frame)
            self.worker.burst_results_signal.connect(self.on_burst_results)
            self.worker.connected_signal.connect(self.on_status_change)
            
            self.btn_connect.setText("연결 종료")
            # 스레드 시작 → connected_signal(1) 수신 후 on_status_change에서 타이머가 시작됩니다.
            self.worker.start()
        else:
            self.trigger_timer.stop()
            self.worker.stop()
            self.worker = None
            self.btn_connect.setText("장치 연결 및 수신 시작")

    def send_test_trigger(self):
        """큐를 통해 장비로 트리거 명령 전송"""
        if self.worker and self.worker.running:
            self.worker.send_command(b't')

    def on_frame(self, qimg, img_id):
        pixmap = QPixmap.fromImage(qimg).scaled(640, 480, Qt.KeepAspectRatio)
        self.lbl_image.setPixmap(pixmap)
        
    def on_burst_results(self, results, cycle_id):
        # 수신된 64바이트 툴 패킷들의 파싱 결과를 출력
        log_str = f"📦 [Burst 수신] Cycle: {cycle_id} | 수신된 툴 개수: {len(results)}\n"
        for r in results:
            log_str += f"   ▶ Tool ID: {r['tool_id']} (Type: {r['tool_type']}) | "
            log_str += f"X:{r['x']:.1f}, Y:{r['y']:.1f}, Angle:{r['angle']:.1f} | "
            log_str += f"Time:{r['proc_time']}ms\n"
            
        self.txt_log.append(log_str)
        # 스크롤 맨 아래로
        self.txt_log.verticalScrollBar().setValue(self.txt_log.verticalScrollBar().maximum())

    def on_status_change(self, state):
        if state == 1:
            # 포트 open + 펌웨어 부팅 대기 완료 → 이제 트리거 전송 시작
            self.trigger_timer.start(100)
        elif state == 0:
            self.trigger_timer.stop()
            self.btn_connect.setText("장치 연결 및 수신 시작")
            self.worker = None

    def log_msg(self, msg, mtype="info"):
        color = "#10b981" if mtype == "success" else "#ef4444" if mtype == "error" else "#38bdf8"
        self.txt_log.append(f"<span style='color:{color}'>[{mtype.upper()}] {msg}</span>")

    def closeEvent(self, event):
        self.trigger_timer.stop()
        if self.worker:
            self.worker.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TestSerialWorker()
    window.show()
    sys.exit(app.exec_())