"""
StatusIndicator.py - 연결 상태 LED 위젯 (v2.0)
DISCONNECTED / CONNECTED / ABNORMAL / VVM 4-State 표시.

HyLink.sig_connected 시그널과 직접 연결:
  0 → DISCONNECTED (회색)
  1 → CONNECTED    (초록)
  2 → ABNORMAL     (빨강)
  3 → VVM          (파랑)
"""
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QPainter, QColor, QBrush, QRadialGradient


class StatusIndicator(QWidget):
    """
    4-State 연결 상태 LED 위젯.
    크기는 생성자 size 인자로 조정 가능 (기본 16×16).

    상태 상수:
        DISCONNECTED = 0  (gray)
        CONNECTED    = 1  (green)
        ABNORMAL     = 2  (red)
        VVM          = 3  (blue)
    """

    DISCONNECTED = 0
    CONNECTED    = 1
    ABNORMAL     = 2
    VVM          = 3

    # 상태 → (기본색, 툴팁 텍스트)
    _STATE_CONFIG = {
        DISCONNECTED: ('#475569', '연결 해제'),
        CONNECTED:    ('#10b981', '장치 연결됨'),
        ABNORMAL:     ('#ef4444', '연결 이상'),
        VVM:          ('#3b82f6', 'VVM 모드'),
    }

    def __init__(self, parent=None, size: int = 16):
        super().__init__(parent)
        self._sz    = size
        self._state = self.DISCONNECTED
        self.setFixedSize(size, size)
        self.setToolTip(self._STATE_CONFIG[self.DISCONNECTED][1])

    # ─────────────────────────────────────────────────────────────────────────
    # 공개 API
    # ─────────────────────────────────────────────────────────────────────────

    def set_state(self, state: int) -> None:
        """상태 설정 후 갱신. HyLink.sig_connected(int) 와 직접 연결 가능."""
        if state not in self._STATE_CONFIG:
            state = self.DISCONNECTED
        self._state = state
        self.setToolTip(self._STATE_CONFIG[state][1])
        self.update()

    @property
    def state(self) -> int:
        return self._state

    def sizeHint(self) -> QSize:
        return QSize(self._sz, self._sz)

    # ─────────────────────────────────────────────────────────────────────────
    # 렌더링
    # ─────────────────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802
        color_hex, _ = self._STATE_CONFIG.get(self._state,
                                              self._STATE_CONFIG[self.DISCONNECTED])
        color = QColor(color_hex)
        sz    = self._sz
        cx    = sz / 2.0
        cy    = sz / 2.0

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 방사형 그라디언트 (중심 밝음 → 테두리 어두움)
        gradient = QRadialGradient(cx, cy, cx, cx * 0.5, cy * 0.5)
        gradient.setColorAt(0.0, color.lighter(160))
        gradient.setColorAt(0.5, color)
        gradient.setColorAt(1.0, color.darker(200))

        painter.setBrush(QBrush(gradient))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(1, 1, sz - 2, sz - 2)

        # 반사 하이라이트 (상단 좌측 타원형 흰 광택)
        highlight = QColor(255, 255, 255, 90)
        painter.setBrush(QBrush(highlight))
        hl_w = max(1, sz * 3 // 8)
        hl_h = max(1, sz // 4)
        hl_x = max(1, sz // 5)
        hl_y = max(1, sz // 8)
        painter.drawEllipse(hl_x, hl_y, hl_w, hl_h)

        painter.end()
