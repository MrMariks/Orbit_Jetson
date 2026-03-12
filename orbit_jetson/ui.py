# -*- coding: utf-8 -*-
"""
Главное окно приложения Orbit_Jetson на PyQt5.
Видеопоток, панель распознанных номеров, карта, кнопки Старт/Стоп.
"""

import hashlib
import logging
import threading
import time
from typing import Optional

import cv2
import numpy as np
from PyQt5.QtCore import QByteArray, QThread, pyqtSignal, Qt, QTimer, QSize
from PyQt5.QtGui import QImage, QPixmap, QFont, QColor, QPainter, QPen
from PyQt5.QtWidgets import QHBoxLayout
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .config import (
    MAX_DISPLAYED_PLATES,
    DETECTION_DEDUP_HOURS,
    CROP_DEDUP_MINUTES,
    TELEMETRY_INTERVAL_SEC,
    ALPR_CONFIDENCE_MIN,
    RECOGNITION_MODES,
    RECOGNITION_MODE_PERSIST_FILE,
)

logger = logging.getLogger(__name__)


def _crop_fingerprint(crop_image: bytes) -> str:
    """Индекс кропа: один и тот же номер в разных кадрах даёт один ключ — не слать дубликаты."""
    try:
        buf = np.frombuffer(crop_image, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
        if img is None or img.size == 0:
            return hashlib.sha256(crop_image).hexdigest()[:16]
        small = cv2.resize(img, (8, 8))
        med = np.median(small)
        bits = (small.ravel() > med).astype(np.uint8).tobytes()
        return hashlib.sha256(bits).hexdigest()[:16]
    except Exception:
        return hashlib.sha256(crop_image).hexdigest()[:16]


# Стили приложения: в стиле сплэш-скрина — тёмный фон, минимализм, закруглённые блоки
STYLESHEET = """
    QMainWindow, QWidget {
        background-color: #0d1117;
    }
    QLabel#titleLabel {
        color: #e6edf3;
        font-size: 16px;
        font-weight: 600;
        padding: 2px 0;
    }
    QWidget#headerBar {
        background-color: #0d1117;
    }
    QLabel#headerTitle {
        color: #e6edf3;
        font-size: 18px;
        font-weight: 600;
        letter-spacing: 0.5px;
    }
    QLabel#sectionLabel {
        color: #8b949e;
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.5px;
        padding: 0 0 6px 0;
    }
    QLabel#sectionHeader {
        background: transparent;
        color: #8b949e;
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.5px;
        padding: 4px 0 2px 0;
    }
    QFrame#videoFrame {
        background-color: #161b22;
        border: 1px solid #30363d;
        border-radius: 12px;
        padding: 2px;
    }
    QLabel#videoLabel {
        background-color: #0d1117;
        border-radius: 10px;
        color: #6e7681;
        font-size: 13px;
        padding: 16px;
    }
    QFrame#sidePanel {
        background-color: #161b22;
        border: 1px solid #30363d;
        border-radius: 12px;
        padding: 14px;
    }
    QFrame#sectionSep {
        background-color: #30363d;
        border: none;
        max-height: 1px;
        margin: 2px 0 6px 0;
    }
    QFrame#platesBlock {
        background-color: #21262d;
        border: none;
        border-radius: 8px;
        padding: 0;
    }
    QFrame#card {
        background-color: #21262d;
        border: 1px solid #30363d;
        border-radius: 10px;
        padding: 12px;
    }
    QListWidget#platesList {
        background-color: transparent;
        border: none;
        outline: none;
        border-radius: 8px;
        padding: 8px 0;
        color: #e6edf3;
        font-size: 13px;
        font-family: 'Consolas', 'Monaco', monospace;
    }
    QListWidget#platesList::item {
        background-color: #1a1a1a;
        color: #e6edf3;
        padding: 6px 10px;
        border: none;
        margin: 4px 8px;
        border-radius: 4px;
    }
    QListWidget#platesList::item:hover {
        background-color: #333333;
    }
    QListWidget#platesList::item:selected {
        background-color: #333333;
        color: #e6edf3;
    }
    QPushButton#btnLoadVideo, QPushButton#btnLoadPhoto {
        background-color: #21262d;
        color: #e6edf3;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 10px 16px;
        font-size: 13px;
    }
    QPushButton#btnLoadVideo:hover, QPushButton#btnLoadPhoto:hover {
        background-color: #30363d;
        border-color: #484f58;
    }
    QPushButton#btnLoadVideo:pressed, QPushButton#btnLoadPhoto:pressed {
        background-color: #161b22;
    }
    QPushButton#btnStart {
        background-color: #238636;
        color: #ffffff;
        border: none;
        border-radius: 8px;
        padding: 12px 24px;
        font-size: 14px;
        font-weight: 600;
    }
    QPushButton#btnStart:hover {
        background-color: #2ea043;
    }
    QPushButton#btnStart:pressed {
        background-color: #196c2e;
    }
    QPushButton#btnStart:disabled {
        background-color: #21262d;
        color: #6e7681;
    }
    QPushButton#btnStop {
        background-color: #E67E22;
        color: #ffffff;
        border: none;
        border-radius: 8px;
        padding: 12px 24px;
        font-size: 14px;
        font-weight: 600;
    }
    QPushButton#btnStop:hover {
        background-color: #EB984E;
    }
    QPushButton#btnStop:pressed {
        background-color: #C0392B;
    }
    QPushButton#btnStop:disabled {
        background-color: #21262d;
        color: #6e7681;
    }
    QPushButton#btnRecord {
        background-color: #d4a72c;
        color: #0d1117;
        border: 2px solid #f0c674;
        border-radius: 8px;
        padding: 12px 24px;
        font-size: 14px;
        font-weight: 700;
    }
    QPushButton#btnRecord:hover {
        background-color: #e6b82e;
        color: #0d1117;
    }
    QPushButton#btnRecord:pressed {
        background-color: #b8921f;
        color: #0d1117;
    }
    QPushButton#btnRecord:disabled {
        background-color: #21262d;
        color: #6e7681;
    }
    QPushButton#btnExit {
        background-color: #da3633;
        color: #fff;
        border: 2px solid #f85149;
        border-radius: 8px;
        padding: 12px 24px;
        font-size: 14px;
        font-weight: 700;
    }
    QPushButton#btnExit:hover {
        background-color: #f85149;
        color: #fff;
    }
    QPushButton#btnExit:pressed {
        background-color: #b62324;
    }
    QLabel#mapPlaceholder {
        background-color: #0d1117;
        border: 1px solid #30363d;
        border-radius: 8px;
        color: #6e7681;
        font-size: 12px;
        padding: 12px;
    }
    QGroupBox {
        font-size: 12px;
        color: #8b949e;
        border: 1px solid #30363d;
        border-radius: 10px;
        margin-top: 8px;
        padding-top: 8px;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 6px;
    }
    QStatusBar {
        background-color: #161b22;
        color: #e6edf3;
        border-top: 1px solid #30363d;
        padding: 4px 8px;
        font-size: 12px;
    }
    QStatusBar QLabel {
        color: #e6edf3;
    }
"""

# Карта: используем folium; для отображения в PyQt — QWebEngineView
try:
    import folium
    from PyQt5.QtWebEngineWidgets import QWebEngineView
    HAS_MAP = True
except ImportError:
    HAS_MAP = False
    folium = None
    QWebEngineView = None

class _SpinnerWidget(QWidget):
    """Анимированный круговой спиннер (рисуется через QPainter)."""
    def __init__(self, size: int = 36, parent=None):
        super().__init__(parent)
        self._angle = 0
        self._size = size
        self._running = True
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)

    def _tick(self):
        self._angle = (self._angle + 12) % 360
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._running:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self._size
        margin = 4
        pen = QPen(QColor("#58a6ff"), 3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(margin, margin, s - 2*margin, s - 2*margin,
                  (self._angle * 16), 270 * 16)
        p.end()

    def stop(self):
        self._running = False
        self._timer.stop()
        self.update()


class SplashWindow(QWidget):
    """
    Фиксированное окно инициализации — без рамки, без перемещения, скруглённые углы.
    Рисует тёмный фон с border-radius через paintEvent (обычный CSS border-radius
    не работает на top-level frameless QWidget в Windows).

    Обновление статусов из фонового потока — через signal _sig_status.
    """

    ITEMS = [
        ("gps",    "Запуск GPS сервера"),
        ("server", "Подключение к серверу"),
        ("camera", "Проверка камеры"),
        ("alpr",   "Загрузка модели ALPR"),
    ]

    _BG = QColor("#0d1117")
    _RADIUS = 18

    # Сигнал для потокобезопасного обновления из background thread
    _sig_status = pyqtSignal(str, str)  # (key, status)

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setFixedSize(500, 420)

        self._item_labels: dict = {}
        self._item_status_labels: dict = {}
        self._statuses: dict = {k: "wait" for k, _ in self.ITEMS}
        self._spinner: Optional[_SpinnerWidget] = None
        self._spinner_text: Optional[QLabel] = None

        self._sig_status.connect(self._apply_status)
        self._build()

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - self.width())  // 2,
            (screen.height() - self.height()) // 2,
        )

    # ---- рисуем фон со скруглёнными углами ----
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(self._BG)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(self.rect(), self._RADIUS, self._RADIUS)
        p.end()

    # запрет перемещения мышью
    def mousePressEvent(self, e):
        e.ignore()

    def mouseMoveEvent(self, e):
        e.ignore()

    def _ss(self, color: str, size: int = 13, bold: bool = False, extra: str = "") -> str:
        """Вспомогательная: stylesheet-строка для QLabel (всегда transparent background)."""
        w = "font-weight:700;" if bold else ""
        return f"background:transparent;color:{color};font-size:{size}px;{w}{extra}"

    # ---- построение UI ----
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(44, 36, 44, 28)
        root.setSpacing(0)

        # --- Шапка ---
        header = QHBoxLayout()
        header.setSpacing(10)
        dot = QLabel("●")
        dot.setStyleSheet(self._ss("#58a6ff", 18))
        header.addWidget(dot)
        col = QVBoxLayout(); col.setSpacing(2)
        title = QLabel("ORBIT JETSON")
        title.setStyleSheet(self._ss("#e6edf3", 22, True, "letter-spacing:2px;"))
        sub = QLabel("ALPR Patrol System")
        sub.setStyleSheet(self._ss("#8b949e", 11, extra="letter-spacing:1px;"))
        col.addWidget(title); col.addWidget(sub)
        header.addLayout(col); header.addStretch()
        root.addLayout(header)
        root.addSpacing(28)

        # --- Спиннер ---
        srow = QHBoxLayout(); srow.setSpacing(14)
        self._spinner = _SpinnerWidget(36)
        srow.addWidget(self._spinner)
        self._spinner_text = QLabel("Инициализация…")
        self._spinner_text.setStyleSheet(self._ss("#58a6ff", 14))
        srow.addWidget(self._spinner_text); srow.addStretch()
        root.addLayout(srow)
        root.addSpacing(24)

        # --- Разделитель ---
        root.addWidget(self._sep())
        root.addSpacing(20)

        # --- Чеклист ---
        for key, text in self.ITEMS:
            row = QHBoxLayout(); row.setSpacing(12)
            dot_lbl = QLabel("●")
            dot_lbl.setStyleSheet(self._ss("#30363d", 14, extra="min-width:16px;"))
            dot_lbl.setAlignment(Qt.AlignVCenter)
            txt_lbl = QLabel(text)
            txt_lbl.setStyleSheet(self._ss("#8b949e", 13))
            txt_lbl.setAlignment(Qt.AlignVCenter)
            st_lbl = QLabel("")
            st_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            st_lbl.setStyleSheet(self._ss("#0d1117", 12, True, "min-width:40px;"))
            row.addWidget(dot_lbl); row.addWidget(txt_lbl, 1); row.addWidget(st_lbl)
            root.addLayout(row)
            root.addSpacing(12)
            self._item_labels[key] = (dot_lbl, txt_lbl)
            self._item_status_labels[key] = st_lbl

        root.addSpacing(10)
        root.addWidget(self._sep())
        root.addSpacing(16)

        ver = QLabel("v1.0.0")
        ver.setStyleSheet(self._ss("#484f58", 11))
        ver.setAlignment(Qt.AlignCenter)
        root.addWidget(ver)
        root.addStretch()

    @staticmethod
    def _sep() -> QFrame:
        s = QFrame()
        s.setFrameShape(QFrame.HLine)
        s.setStyleSheet("background:#21262d;border:none;max-height:1px;")
        return s

    # ---- публичный API (потокобезопасный) ----
    def update_loading_status(self, key: str, status: str) -> None:
        """Можно вызывать из ЛЮБОГО потока — сигнал доставит в GUI-поток."""
        self._sig_status.emit(key, status)

    def _apply_status(self, key: str, status: str) -> None:
        """Слот: применяет статус в GUI-потоке (вызывается через _sig_status)."""
        if key not in self._item_labels:
            return
        self._statuses[key] = status
        dot_lbl, txt_lbl = self._item_labels[key]
        st_lbl = self._item_status_labels[key]

        if status == "ok":
            dot_lbl.setStyleSheet(self._ss("#3fb950", 14, extra="min-width:16px;"))
            txt_lbl.setStyleSheet(self._ss("#e6edf3", 13))
            st_lbl.setStyleSheet(self._ss("#3fb950", 12, True, "min-width:40px;"))
            st_lbl.setText("OK")
        elif status == "fail":
            dot_lbl.setStyleSheet(self._ss("#f85149", 14, extra="min-width:16px;"))
            txt_lbl.setStyleSheet(self._ss("#e6edf3", 13))
            st_lbl.setStyleSheet(self._ss("#f85149", 12, True, "min-width:40px;"))
            st_lbl.setText("✗")
        elif status == "loading":
            dot_lbl.setStyleSheet(self._ss("#58a6ff", 14, extra="min-width:16px;"))
            txt_lbl.setStyleSheet(self._ss("#58a6ff", 13))
            st_lbl.setStyleSheet(self._ss("#58a6ff", 12, True, "min-width:40px;"))
            st_lbl.setText("...")
        else:
            dot_lbl.setStyleSheet(self._ss("#30363d", 14, extra="min-width:16px;"))
            txt_lbl.setStyleSheet(self._ss("#8b949e", 13))
            st_lbl.setStyleSheet(self._ss("#0d1117", 12, True, "min-width:40px;"))
            st_lbl.setText("")

        self._sync_spinner()

    def _sync_spinner(self) -> None:
        """Текст спиннера = первый (сверху вниз) пункт со статусом 'loading'.
        Если нет loading — показать 'Готово' и остановить анимацию."""
        if not self._spinner_text:
            return
        for key, label in self.ITEMS:
            if self._statuses.get(key) == "loading":
                self._spinner_text.setText(f"{label}…")
                return
        # Нет ни одного "loading"
        all_done = all(self._statuses.get(k) in ("ok", "fail") for k, _ in self.ITEMS)
        if all_done:
            self._spinner_text.setText("Готово")
            if self._spinner:
                self._spinner.stop()

    def mark_done(self) -> None:
        """Вызывается из main.py как финальная гарантия — спиннер точно остановлен."""
        if self._spinner:
            self._spinner.stop()
        if self._spinner_text:
            self._spinner_text.setText("Готово")

    def get_statuses(self) -> dict:
        """Вернуть копию текущих статусов загрузки (для передачи в главное окно)."""
        return dict(self._statuses)


class CameraThread(QThread):
    """Поток захвата камеры: открывает камеру в этом же потоке и читает кадры (важно для Iriun)."""

    frame_ready = pyqtSignal(object)  # JPEG bytes (object для совместимости PyQt5 + Python 3.12)
    plate_detected = pyqtSignal(str, float, float, object, object)  # plate_number, lat, lon, full_image, crop_image
    open_failed = pyqtSignal()  # камера не открылась

    def __init__(self, camera_worker, parent=None):
        super().__init__(parent)
        self._camera = camera_worker
        self._stopped = False

    def run(self):
        import sys
        self._stopped = False
        # Открываем камеру в этом же потоке — иначе Iriun/виртуальная камера может не отдавать кадры
        if not self._camera.start():
            self.open_failed.emit()
            return
        first_frame_logged = False
        while not self._stopped and self._camera.is_running():
            try:
                ok, data = self._camera.read_frame()
            except Exception as e:
                logger.warning("Ошибка чтения кадра (источник видео/камера): %s", e)
                self.msleep(100)
                continue
            if ok and data:
                if not first_frame_logged:
                    first_frame_logged = True
                    logger.info("Камера: первый кадр прочитан, картинка в окне и для ALPR")
                    sys.stdout.flush()
                    sys.stderr.flush()
                self.frame_ready.emit(data)
            else:
                self.msleep(20)
        self._camera.stop()

    def stop(self):
        self._stopped = True


class MainWindow(QMainWindow):
    """Главное окно: видео, номера, карта, Старт/Стоп."""

    def __init__(self, camera_worker, api_client, gps_stub, initial_splash_statuses=None, parent=None):
        super().__init__(parent)
        self._camera = camera_worker
        self._api = api_client
        self._gps = gps_stub
        # Статусы из сплэш-окна: gps, server, camera, alpr -> ok/fail/loading/wait
        self._splash_statuses = dict(initial_splash_statuses) if initial_splash_statuses else {}
        self._camera_thread: Optional[CameraThread] = None
        self._display_timer = None
        self._latest_frame_bytes: Optional[bytes] = None  # последний кадр для плавного вывода
        self._map_view = None  # QWebEngineView or QLabel
        self._sent_plates: dict = {}  # нормализованный номер -> время последней отправки на сервер
        self._last_sent_position: Optional[tuple] = None  # последнее отправленное положение (lat, lon)
        self.setWindowTitle("Orbit_Jetson — Патрульный модуль")
        self.setStyleSheet(STYLESHEET)
        self._build_ui()

    def _build_ui(self):
        main_content = QWidget()
        main_layout = QVBoxLayout(main_content)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Шапка 40px: слева текст, справа статус-кружки
        header_bar = QWidget()
        header_bar.setObjectName("headerBar")
        header_bar.setFixedHeight(40)
        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(16, 0, 16, 0)
        header_layout.setSpacing(0)
        header_title = QLabel("ORBIT JETSON — Patrol System")
        header_title.setObjectName("headerTitle")
        header_layout.addWidget(header_title)
        header_layout.addStretch()
        self._status_widget = QWidget()
        status_row = QHBoxLayout(self._status_widget)
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(16)
        self._status_labels = {}
        for key, name in [("po", "ПО"), ("model", "Модель"), ("server", "Сервер"), ("camera", "Камера"), ("gps", "GPS")]:
            wrap = QWidget()
            h = QHBoxLayout(wrap)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            dot = QLabel("●")
            dot.setObjectName("statusDot_" + key)
            dot.setStyleSheet("color: #30363d; font-size: 14px;")
            lbl = QLabel(name)
            lbl.setStyleSheet("color: #8b949e; font-size: 14px; font-weight: 500;")
            h.addWidget(dot)
            h.addWidget(lbl)
            status_row.addWidget(wrap)
            self._status_labels[key] = dot
        header_layout.addWidget(self._status_widget)
        main_layout.addWidget(header_bar)

        # Разделительная линия на всю ширину окна (включая область правой панели)
        header_sep = QFrame()
        header_sep.setFixedHeight(1)
        header_sep.setStyleSheet("background-color: #30363d;")
        header_sep.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        main_layout.addWidget(header_sep)

        content = QGridLayout()
        content.setHorizontalSpacing(12)
        content.setVerticalSpacing(12)
        # Верхний отступ как у зазора между камерой и кнопками (12px)
        content.setContentsMargins(16, 12, 16, 0)

        # Окно камеры — заполняет всю левую область от шапки до кнопок без обрезания
        video_frame = QFrame()
        video_frame.setObjectName("videoFrame")
        video_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        video_layout = QVBoxLayout(video_frame)
        video_layout.setContentsMargins(2, 2, 2, 2)
        self._video_label = QLabel()
        self._video_label.setObjectName("videoLabel")
        self._video_label.setAlignment(Qt.AlignCenter)
        self._video_label.setText("Запустите мониторинг")
        self._video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._video_label.setMinimumSize(320, 240)
        self._video_label.setScaledContents(False)
        video_layout.addWidget(self._video_label)
        content.addWidget(video_frame, 0, 0, 1, 1)
        content.setRowStretch(0, 1)
        content.setRowStretch(1, 0)

        # Кнопки вплотную под окном камеры, без зазора снизу
        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(12)
        self._btn_start = QPushButton("▶  Старт")
        self._btn_start.setObjectName("btnStart")
        self._btn_start.setMinimumHeight(48)
        self._btn_start.setCursor(Qt.PointingHandCursor)
        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop = QPushButton("■  Стоп")
        self._btn_stop.setObjectName("btnStop")
        self._btn_stop.setMinimumHeight(48)
        self._btn_stop.setCursor(Qt.PointingHandCursor)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_stop.setEnabled(False)
        self._btn_exit = QPushButton("Выход")
        self._btn_exit.setObjectName("btnExit")
        self._btn_exit.setMinimumHeight(48)
        self._btn_exit.setCursor(Qt.PointingHandCursor)
        self._btn_exit.setToolTip("Закрыть ПО и сообщить серверу о завершении патруля")
        self._btn_exit.clicked.connect(self._on_exit)
        btn_layout.addWidget(self._btn_start, 1)
        btn_layout.addWidget(self._btn_stop, 1)
        btn_layout.addWidget(self._btn_exit, 1)
        content.addWidget(btn_row, 1, 0, 1, 1)

        # Правая панель — на всю высоту окна; карта растягивается и заполняет остаток снизу
        right = QFrame()
        right.setObjectName("sidePanel")
        right.setFixedWidth(360)
        right_layout = QVBoxLayout(right)
        right_layout.setSpacing(6)
        right_layout.setContentsMargins(14, 20, 14, 14)

        def _add_section(title: str):
            """Заголовок блока: текст + разделитель (без тёмной заливки)."""
            h = QLabel(title)
            h.setObjectName("sectionHeader")
            right_layout.addWidget(h)
            sep = QFrame()
            sep.setObjectName("sectionSep")
            sep.setFrameShape(QFrame.HLine)
            right_layout.addWidget(sep)

        # 1. Настройки
        _add_section("Настройки")
        card_settings = QFrame()
        card_settings.setObjectName("card")
        si = QVBoxLayout(card_settings)
        si.setSpacing(6)
        self._combo_mode = QComboBox()
        for value, label in RECOGNITION_MODES:
            self._combo_mode.addItem(label, value)
        self._combo_mode.setStyleSheet("background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 6px; color: #e6edf3; min-height: 20px;")
        self._combo_mode.currentIndexChanged.connect(self._on_mode_changed)
        self._load_persisted_mode()
        si.addWidget(self._combo_mode)
        lbl_c = QLabel("Уверенность (0–1)")
        lbl_c.setStyleSheet("color: #8b949e; font-size: 11px;")
        si.addWidget(lbl_c)
        self._spin_confidence = QDoubleSpinBox()
        self._spin_confidence.setRange(0.50, 1.0)
        self._spin_confidence.setSingleStep(0.01)
        self._spin_confidence.setDecimals(2)
        self._spin_confidence.setValue(float(ALPR_CONFIDENCE_MIN))
        self._spin_confidence.setStyleSheet("background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 6px; color: #e6edf3; min-height: 20px;")
        self._spin_confidence.valueChanged.connect(self._on_confidence_changed)
        si.addWidget(self._spin_confidence)
        right_layout.addWidget(card_settings)

        # 2. Запись — кнопки вертикально (Видео, Фото, Запись)
        _add_section("Запись")
        card_record = QFrame()
        card_record.setObjectName("card")
        ri = QVBoxLayout(card_record)
        ri.setSpacing(6)
        self._btn_load_video = QPushButton("Видео")
        self._btn_load_video.setObjectName("btnLoadVideo")
        self._btn_load_video.setToolTip("Загрузить видео (DEBUG)")
        self._btn_load_video.setCursor(Qt.PointingHandCursor)
        self._btn_load_video.clicked.connect(self._on_load_video)
        self._btn_load_photo = QPushButton("Фото")
        self._btn_load_photo.setObjectName("btnLoadPhoto")
        self._btn_load_photo.setToolTip("Загрузить фото (DEBUG)")
        self._btn_load_photo.setCursor(Qt.PointingHandCursor)
        self._btn_load_photo.clicked.connect(self._on_load_photo)
        self._btn_record = QPushButton("Запись")
        self._btn_record.setObjectName("btnRecord")
        self._btn_record.setToolTip("Сохранять видео с рамками")
        self._btn_record.setCursor(Qt.PointingHandCursor)
        self._btn_record.clicked.connect(self._on_toggle_record)
        self._btn_record.setEnabled(False)
        ri.addWidget(self._btn_load_video)
        ri.addWidget(self._btn_load_photo)
        ri.addWidget(self._btn_record)
        right_layout.addWidget(card_record)

        # 3. Номера — занимает всё освободившееся место между Запись и картой
        _add_section("Номера")
        card_plates = QFrame()
        card_plates.setObjectName("platesBlock")
        plate_inner = QVBoxLayout(card_plates)
        plate_inner.setContentsMargins(0, 0, 0, 0)
        plate_inner.setSpacing(0)
        self._plates_list = QListWidget()
        self._plates_list.setObjectName("platesList")
        self._plates_list.setMinimumHeight(80)
        plate_inner.addWidget(self._plates_list)
        right_layout.addWidget(card_plates, 1)

        # 4. Карта — фиксированная высота 180px, не растягивать
        _add_section("Карта")
        card_map = QFrame()
        card_map.setObjectName("card")
        map_inner = QVBoxLayout(card_map)
        map_inner.setSpacing(0)
        map_inner.setContentsMargins(0, 0, 0, 0)
        if HAS_MAP and QWebEngineView:
            self._map_view = QWebEngineView()
            self._map_view.setFixedHeight(180)
            self._map_view.setMinimumWidth(200)
            self._map_view.setStyleSheet("background: #0d1117; border-radius: 8px; border: 1px solid #30363d;")
            map_inner.addWidget(self._map_view)
        else:
            self._map_view = QLabel("Карта (folium + PyQtWebEngine)")
            self._map_view.setObjectName("mapPlaceholder")
            self._map_view.setFixedHeight(180)
            self._map_view.setMinimumWidth(200)
            map_inner.addWidget(self._map_view)
        right_layout.addWidget(card_map)

        content.addWidget(right, 0, 1, 2, 1)
        main_layout.addLayout(content)

        # Таймеры
        self._last_status_str: Optional[str] = None
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status_bar)
        self._status_timer.start(2000)
        # Периодический пинг сервера для актуального статуса (раз в 10 сек)
        self._server_check_timer = QTimer(self)
        self._server_check_timer.timeout.connect(self._trigger_server_check)
        self._server_check_timer.start(10000)
        self._telemetry_timer = QTimer(self)
        self._telemetry_timer.timeout.connect(self._send_telemetry)
        self._update_status_bar()
        self._update_map()

        if hasattr(self._gps, "add_position_listener"):
            def _on_gps_position_changed(lat: float, lon: float) -> None:
                def _apply(la=lat, lo=lon):
                    self._update_map(lat=la, lon=lo)
                    self._send_position(lat=la, lon=lo, only_if_changed=True)
                QTimer.singleShot(0, _apply)
            self._gps.add_position_listener(_on_gps_position_changed)

        self.setCentralWidget(main_content)

    def _trigger_server_check(self) -> None:
        """Фоновая проверка доступности сервера (для кружка Сервер)."""
        threading.Thread(target=self._api.check_server, daemon=True).start()

    def set_splash_statuses(self, statuses: dict) -> None:
        """Принять статусы из окна загрузки. Вызывается из main.py перед показом окна."""
        self._splash_statuses = dict(statuses) if statuses else {}
        self._trigger_server_check()  # сразу один пинг для кружка Сервер
        self._update_status_bar()

    def _load_persisted_mode(self) -> None:
        """Восстановить выбранный режим из файла (между сессиями)."""
        try:
            raw = RECOGNITION_MODE_PERSIST_FILE.read_text(encoding="utf-8").strip()
            for i in range(self._combo_mode.count()):
                if self._combo_mode.itemData(i) == raw:
                    self._combo_mode.blockSignals(True)
                    self._combo_mode.setCurrentIndex(i)
                    self._combo_mode.blockSignals(False)
                    self._camera.set_detection_only(raw == "detection_only")
                    return
        except Exception:
            pass

    def _save_persisted_mode(self) -> None:
        """Сохранить выбранный режим в файл."""
        try:
            value = self._combo_mode.currentData()
            if value:
                RECOGNITION_MODE_PERSIST_FILE.write_text(value, encoding="utf-8")
        except Exception:
            pass

    def _on_camera_open_failed(self):
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_record.setEnabled(False)
        self._video_label.setText("Ошибка: не удалось открыть камеру")
        self._update_status_bar()

    def _on_mode_changed(self, index: int):
        value = self._combo_mode.currentData()
        self._camera.set_detection_only(value == "detection_only")
        self._save_persisted_mode()

    def _on_confidence_changed(self, value: float):
        self._camera.set_confidence_min(value)

    def _on_load_video(self):
        # DEBUG ONLY — логика в debug_video_loader
        try:
            from .debug_video_loader import get_video_path, set_video_source
        except ImportError:
            return
        path = get_video_path(self)
        if path:
            set_video_source(self._camera, path)
            import os
            self.setStatusTip("Видео: " + os.path.basename(path))

    def _on_load_photo(self):
        # DEBUG ONLY — логика в debug_photo_loader
        try:
            from .debug_photo_loader import get_photo_path, recognize_photo
        except ImportError:
            return
        path = get_photo_path(self)
        if not path:
            return
        self._video_label.setText("Распознавание…")
        self._video_label.repaint()

        def run():
            results = recognize_photo(self._camera, path)
            QTimer.singleShot(0, lambda: self._on_photo_results(path, results))

        threading.Thread(target=run, daemon=True).start()

    def _on_photo_results(self, path: str, results: list):
        self._video_label.setText("Запустите мониторинг")
        pos = self._gps.get_position()
        lat = pos.latitude or 0.0
        lon = pos.longitude or 0.0
        for plate, full_bytes, crop_bytes in results:
            self._on_plate(plate, lat, lon, full_bytes, crop_bytes)
        if not results:
            self.setStatusTip("Номер не распознан")
        else:
            self.setStatusTip("Фото: %s — %s" % (path, ", ".join(r[0] for r in results)))

    def _update_status_bar(self):
        """
        Кружки статус-бара: зелёный = работает, красный = ошибка/недоступно, серый = неизвестно.
        Источники данных:
          ПО      — всегда ок (приложение запущено).
          Модель  — camera.is_alpr_loaded(): загрузка ALPR (splash: alpr ok/fail).
          Сервер  — api._server_reachable после api.check_server() (пинг endpoint).
          Камера  — при мониторинге: camera.is_running() и camera.has_frame() (активный поток).
          GPS     — gps.is_connected() (данные с браузера/моста).
        """
        # ПО — всегда зелёный, главное окно открыто
        po_ok = True

        # Модель — camera.is_alpr_loaded()
        model_loaded = self._camera.is_alpr_loaded()
        model_ok = model_loaded
        model_fail = not model_loaded  # после загрузки знаем: либо ок, либо fail

        # Сервер — api.check_server() обновляет _server_reachable (пинг endpoint)
        server_reachable = getattr(self._api, "_server_reachable", False)
        server_ok = server_reachable
        server_fail = not server_reachable

        # Камера — при запущенном мониторинге: есть ли кадр
        running = self._camera.is_running()
        if not running:
            camera_ok, camera_fail = False, False  # серый
        else:
            has_frame = self._camera.has_frame()
            camera_ok = has_frame
            camera_fail = not has_frame

        # GPS — gps.is_connected()
        gps_connected = getattr(self._gps, "is_connected", lambda: None)()
        if gps_connected is None:
            gps_ok, gps_fail = False, False  # серый (заглушка без is_connected)
        else:
            gps_ok = bool(gps_connected)
            gps_fail = not gps_connected

        states = [
            ("po", po_ok, False),
            ("model", model_ok, model_fail),
            ("server", server_ok, server_fail),
            ("camera", camera_ok, camera_fail),
            ("gps", gps_ok, gps_fail),
        ]
        for key, ok, fail in states:
            dot = self._status_labels.get(key)
            if not dot:
                continue
            if ok:
                dot.setStyleSheet("color: #3fb950; font-size: 14px;")  # зелёный
            elif fail:
                dot.setStyleSheet("color: #f85149; font-size: 14px;")  # красный
            else:
                dot.setStyleSheet("color: #30363d; font-size: 14px;")  # серый

        status_str = "ПО %s | Модель %s | Сервер %s | Камера %s | GPS %s" % (
            "✓" if po_ok else "—",
            "✓" if model_ok else ("✗" if model_fail else "…"),
            "✓" if server_ok else ("✗" if server_fail else "…"),
            "✓" if camera_ok else ("✗" if camera_fail else "…"),
            "✓" if gps_ok else ("✗" if gps_fail else "…"),
        )
        if status_str != self._last_status_str:
            self._last_status_str = status_str
            log_str = "ПО %s | Модель %s | Сервер %s | Камера %s | GPS %s" % (
                "да" if po_ok else "нет",
                "да" if model_ok else ("нет" if model_fail else "—"),
                "да" if server_ok else ("нет" if server_fail else "—"),
                "да" if camera_ok else ("нет" if camera_fail else "—"),
                "да" if gps_ok else ("нет" if gps_fail else "—"),
            )
            logger.info(log_str)

    def _on_start(self):
        # Камера открывается внутри CameraThread (нужно для Iriun)
        self._camera_thread = CameraThread(self._camera)
        self._camera_thread.frame_ready.connect(self._on_frame)
        self._camera_thread.plate_detected.connect(self._on_plate)
        self._camera_thread.open_failed.connect(self._on_camera_open_failed)
        self._camera.set_gps(None, None)
        # Callback из camera worker вызывается из потока — передаём в main thread через сигнал
        def on_plate(text: str, lat, lon, full_image: bytes, crop_image: bytes):
            self._camera_thread.plate_detected.emit(text, lat or 0.0, lon or 0.0, full_image, crop_image)
        self._camera.on_plate_detected = on_plate
        self._video_label.setText("Открытие камеры…")
        self._video_label.repaint()
        self._latest_frame_bytes = None
        self._camera_thread.start()
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._btn_record.setEnabled(True)
        self._btn_record.setText("●  Запись")
        self._last_sent_position = None
        self._update_status_bar()
        # Вывод видео: обновление каждые 25 ms (~40 fps), меньше лагов
        self._display_timer = QTimer(self)
        self._display_timer.timeout.connect(self._paint_latest_frame)
        self._display_timer.start(25)
        self._telemetry_timer.start(int(TELEMETRY_INTERVAL_SEC * 1000))
        # Проверка доступности сервера в фоне — тогда в статус-баре сразу «Сервер ✓»
        threading.Thread(target=self._api.check_server, daemon=True).start()
        # Уведомление сервера: патруль активен
        threading.Thread(target=self._api.send_patrol_active, daemon=True).start()

    def _on_stop(self):
        if self._display_timer:
            self._display_timer.stop()
            self._display_timer = None
        self._telemetry_timer.stop()
        self._latest_frame_bytes = None
        self._camera.stop_recording()
        self._btn_record.setEnabled(False)
        self._btn_record.setText("●  Запись")
        if self._camera_thread:
            self._camera_thread.stop()
            self._camera_thread.wait(3000)
            self._camera_thread = None
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._video_label.setText("Запустите мониторинг")
        self._update_status_bar()

    def _on_exit(self):
        """Остановить мониторинг, сообщить серверу о завершении патруля и закрыть приложение."""
        if self._camera.is_running():
            self._on_stop()
        self._api.send_patrol_finish()
        QApplication.instance().quit()

    def _on_toggle_record(self):
        if self._camera.is_recording():
            path = self._camera.stop_recording()
            self._btn_record.setText("●  Запись")
            if path:
                self.setStatusTip(f"Запись сохранена: {path}")
        else:
            path = self._camera.start_recording()
            if path:
                self._btn_record.setText("■  Стоп запись")
                self.setStatusTip(f"Запись: {path}")
            else:
                self.setStatusTip("Не удалось начать запись")

    def _on_frame(self, jpeg_bytes: bytes):
        """Сохраняем последний кадр; отрисовка по таймеру для плавности."""
        self._latest_frame_bytes = jpeg_bytes

    def _paint_latest_frame(self):
        """По таймеру выводим последний кадр. FastTransformation — меньше лагов."""
        if not self._latest_frame_bytes:
            return
        img = QImage()
        img.loadFromData(QByteArray(self._latest_frame_bytes))
        if not img.isNull():
            self._video_label.setPixmap(QPixmap.fromImage(img).scaled(
                self._video_label.size(), Qt.KeepAspectRatio, Qt.FastTransformation))

    def _on_plate(self, plate: str, lat: float, lon: float, full_image: bytes, crop_image: bytes):
        ts = time.strftime("%H:%M:%S", time.localtime())
        display_text = plate if plate else "— (на сервере)"
        item = QListWidgetItem(f"{display_text}  —  {ts}")
        self._plates_list.insertItem(0, item)
        while self._plates_list.count() > MAX_DISPLAYED_PLATES:
            self._plates_list.takeItem(self._plates_list.count() - 1)
        # Дедуп по тексту номера или по хешу кропа (для «не удалось распознать» и пустого)
        if plate and plate.strip() and plate.strip() != "не удалось распознать":
            key = plate.strip().upper().replace(" ", "")
            dedup_sec = DETECTION_DEDUP_HOURS * 3600
        else:
            key = "crop:" + _crop_fingerprint(crop_image)
            dedup_sec = CROP_DEDUP_MINUTES * 60
        now = time.time()
        if key in self._sent_plates and (now - self._sent_plates[key]) < dedup_sec:
            logger.info("[Сервер] Пропуск дубликата: уже отправлялось (ключ %s), на сервер не шлём", key[:20] + "…" if len(key) > 20 else key)
        else:
            self._sent_plates[key] = now
            logger.info("[Сервер] Обнаружен номер %s — отправляю на сервер", display_text)
            threading.Thread(
                target=self._send_detection_background,
                args=(plate, full_image, crop_image, display_text),
                daemon=True,
            ).start()
            plate_dedup_sec = DETECTION_DEDUP_HOURS * 3600
            crop_dedup_sec = CROP_DEDUP_MINUTES * 60
            for k in list(self._sent_plates):
                limit = crop_dedup_sec * 2 if k.startswith("crop:") else plate_dedup_sec * 2
                if now - self._sent_plates[k] > limit:
                    del self._sent_plates[k]
        pos = self._gps.get_position()
        self._camera.set_gps(pos.latitude, pos.longitude)

    def _send_detection_background(self, plate: str, full_image: bytes, crop_image: bytes, display_text: str) -> None:
        """Отправка детекции на сервер в фоне, чтобы не блокировать интерфейс."""
        ok = self._api.send_detection(plate, full_image, crop_image)
        if not ok:
            logger.warning("[Сервер] Отправка не удалась для номера %s (см. сообщения выше)", display_text)

    def _send_telemetry(self):
        pos = self._gps.get_position()
        self._api.send_telemetry(pos)
        self._camera.set_gps(pos.latitude, pos.longitude)

    def _send_position(self, lat: float = None, lon: float = None, only_if_changed: bool = False):
        """Отправка координат патруля на POST /api/v1/patrol/position."""
        if lat is None or lon is None:
            pos = self._gps.get_position()
            lat, lon = pos.latitude, pos.longitude
        if lat is None or lon is None:
            return
        if not self._camera.is_running():
            return
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return
        point = (round(float(lat), 7), round(float(lon), 7))
        if only_if_changed and self._last_sent_position == point:
            return
        self._api.send_patrol_position(lat, lon)
        self._last_sent_position = point
        self._camera.set_gps(lat, lon)

    def _update_map(self, lat: float = None, lon: float = None):
        if not HAS_MAP or not folium:
            return
        if lat is None or lon is None:
            pos = self._gps.get_position()
            lat, lon = pos.latitude, pos.longitude
        try:
            # Тёмная тема тайлов (без кнопок +/- в стиле)
            m = folium.Map(
                location=[lat, lon],
                zoom_start=15,
                tiles="Cartodb dark_matter",
                attr="CartoDB",
                zoom_control=False,
            )
            folium.Marker([lat, lon], popup="Патруль").add_to(m)
            html = m.get_root().render()
            if HAS_MAP and QWebEngineView is not None and hasattr(self._map_view, "setHtml"):
                self._map_view.setHtml(html)
            elif hasattr(self._map_view, "setText"):
                self._map_view.setText(f"GPS: {lat:.5f}, {lon:.5f}")
        except Exception as e:
            logger.debug("Map update error: %s", e)
