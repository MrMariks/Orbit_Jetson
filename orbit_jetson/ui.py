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

from PyQt5.QtCore import QByteArray, QThread, pyqtSignal, Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap, QFont
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .config import MAX_DISPLAYED_PLATES, DETECTION_DEDUP_HOURS, CROP_DEDUP_MINUTES

logger = logging.getLogger(__name__)

# Стили приложения: тёмная тема «патрульный модуль»
STYLESHEET = """
    QMainWindow, QWidget {
        background-color: #0f1419;
    }
    QLabel#titleLabel {
        color: #e6edf3;
        font-size: 18px;
        font-weight: 600;
        padding: 4px 0;
    }
    QLabel#sectionLabel {
        color: #8b949e;
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        padding: 0 0 6px 0;
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
        font-size: 14px;
        padding: 24px;
    }
    QFrame#sidePanel {
        background-color: #161b22;
        border: 1px solid #30363d;
        border-radius: 12px;
        padding: 16px;
    }
    QFrame#card {
        background-color: #21262d;
        border: 1px solid #30363d;
        border-radius: 10px;
        padding: 12px;
    }
    QListWidget#platesList {
        background-color: #0d1117;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 8px;
        color: #e6edf3;
        font-size: 13px;
        font-family: 'Consolas', 'Monaco', monospace;
        outline: none;
    }
    QListWidget#platesList::item {
        padding: 6px 8px;
        border-radius: 6px;
        margin: 2px 0;
    }
    QListWidget#platesList::item:hover {
        background-color: #30363d;
    }
    QListWidget#platesList::item:selected {
        background-color: #388bfd;
        color: #fff;
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
        background-color: #da3633;
        color: #ffffff;
        border: none;
        border-radius: 8px;
        padding: 12px 24px;
        font-size: 14px;
        font-weight: 600;
    }
    QPushButton#btnStop:hover {
        background-color: #f85149;
    }
    QPushButton#btnStop:pressed {
        background-color: #b62324;
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
    QLabel#mapPlaceholder {
        background-color: #0d1117;
        border: 1px solid #30363d;
        border-radius: 8px;
        color: #6e7681;
        font-size: 12px;
        padding: 12px;
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

def _make_loading_page() -> QWidget:
    """Виджет страницы «Загрузка…» (тот же стиль, что и основное окно)."""
    page = QWidget()
    page.setStyleSheet(STYLESHEET)
    layout = QVBoxLayout(page)
    layout.setSpacing(20)
    layout.setContentsMargins(40, 40, 40, 40)
    layout.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
    title = QLabel("Патрульный модуль")
    title.setObjectName("titleLabel")
    layout.addWidget(title)
    status = QLabel("Подготовка модели распознавания номеров и проверка сервера…")
    status.setStyleSheet("color: #e6edf3; font-size: 15px;")
    layout.addWidget(status)
    hint = QLabel("Интерфейс переключится автоматически по готовности.")
    hint.setStyleSheet("color: #8b949e; font-size: 13px;")
    layout.addWidget(hint)
    return page


class CameraThread(QThread):
    """Поток захвата камеры: открывает камеру в этом же потоке и читает кадры (важно для Iriun)."""

    frame_ready = pyqtSignal(bytes)  # JPEG bytes
    plate_detected = pyqtSignal(str, float, float, bytes, bytes)  # plate_number, lat, lon, full_image, crop_image
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

    def __init__(self, camera_worker, api_client, gps_stub, parent=None):
        super().__init__(parent)
        self._camera = camera_worker
        self._api = api_client
        self._gps = gps_stub
        self._camera_thread: Optional[CameraThread] = None
        self._display_timer = None
        self._latest_frame_bytes: Optional[bytes] = None  # последний кадр для плавного вывода
        self._map_view = None  # QWebEngineView or QLabel
        self._sent_plates: dict = {}  # нормализованный номер -> время последней отправки на сервер
        self.setWindowTitle("Orbit_Jetson — Патрульный модуль")
        self.setStyleSheet(STYLESHEET)
        self._stack: Optional[QStackedWidget] = None
        self._build_ui()

    def show_main_content(self) -> None:
        """Переключить на основной интерфейс (после загрузки модели и проверки сервера)."""
        if self._stack is not None:
            self._stack.setCurrentIndex(1)

    def _build_ui(self):
        loading_page = _make_loading_page()
        main_content = QWidget()
        main_layout = QVBoxLayout(main_content)
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(20, 16, 20, 16)

        # Заголовок
        title = QLabel("Патрульный модуль")
        title.setObjectName("titleLabel")
        main_layout.addWidget(title)

        content = QGridLayout()
        content.setSpacing(16)

        # Видео — в рамке
        video_frame = QFrame()
        video_frame.setObjectName("videoFrame")
        video_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        video_layout = QVBoxLayout(video_frame)
        video_layout.setContentsMargins(4, 4, 4, 4)
        self._video_label = QLabel()
        self._video_label.setObjectName("videoLabel")
        self._video_label.setMinimumSize(640, 480)
        self._video_label.setAlignment(Qt.AlignCenter)
        self._video_label.setText(
            "Видеопоток\nЗапустите мониторинг\n\n"
            "Для Iriun: закройте окно приложения Iriun Webcam на ПК\n(телефон оставьте с включённой передачей)"
        )
        self._video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        video_layout.addWidget(self._video_label)
        content.addWidget(video_frame, 0, 0, 2, 1)

        # Правая колонка: карточки
        right = QFrame()
        right.setObjectName("sidePanel")
        right.setMaximumWidth(340)
        right_layout = QVBoxLayout(right)
        right_layout.setSpacing(14)

        # Блок «Распознанные номера»
        card_plates = QFrame()
        card_plates.setObjectName("card")
        plate_inner = QVBoxLayout(card_plates)
        plate_inner.setSpacing(8)
        lbl_plates = QLabel("Распознанные номера")
        lbl_plates.setObjectName("sectionLabel")
        plate_inner.addWidget(lbl_plates)
        self._plates_list = QListWidget()
        self._plates_list.setObjectName("platesList")
        self._plates_list.setMaximumHeight(200)
        plate_inner.addWidget(self._plates_list)
        right_layout.addWidget(card_plates)

        # Блок «Местоположение»
        card_map = QFrame()
        card_map.setObjectName("card")
        map_inner = QVBoxLayout(card_map)
        map_inner.setSpacing(8)
        lbl_map = QLabel("Местоположение")
        lbl_map.setObjectName("sectionLabel")
        map_inner.addWidget(lbl_map)
        if HAS_MAP and QWebEngineView:
            self._map_view = QWebEngineView()
            self._map_view.setMinimumSize(300, 220)
            self._map_view.setMaximumHeight(240)
            self._map_view.setStyleSheet("background: #0d1117; border-radius: 8px; border: 1px solid #30363d;")
            map_inner.addWidget(self._map_view)
        else:
            self._map_view = QLabel("Карта (установите folium и PyQtWebEngine)")
            self._map_view.setObjectName("mapPlaceholder")
            self._map_view.setMinimumSize(300, 220)
            map_inner.addWidget(self._map_view)
        right_layout.addWidget(card_map)

        content.addWidget(right, 0, 1, 2, 1)

        # Кнопки внизу
        btn_layout = QGridLayout()
        btn_layout.setSpacing(12)
        self._btn_start = QPushButton("▶  Старт мониторинга")
        self._btn_start.setObjectName("btnStart")
        self._btn_start.setCursor(Qt.PointingHandCursor)
        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop = QPushButton("■  Стоп")
        self._btn_stop.setObjectName("btnStop")
        self._btn_stop.setCursor(Qt.PointingHandCursor)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_stop.setEnabled(False)
        self._btn_record = QPushButton("●  Запись")
        self._btn_record.setObjectName("btnRecord")
        self._btn_record.setToolTip("Сохранять видео с рамками на номерах для последующего анализа")
        self._btn_record.setCursor(Qt.PointingHandCursor)
        self._btn_record.clicked.connect(self._on_toggle_record)
        self._btn_record.setEnabled(False)
        btn_layout.addWidget(self._btn_start, 0, 0)
        btn_layout.addWidget(self._btn_stop, 0, 1)
        btn_layout.addWidget(self._btn_record, 0, 2)
        content.addLayout(btn_layout, 2, 0, 1, 2)

        main_layout.addLayout(content)

        # Статус-бар: одна компактная строка (ПО, модель, сервер, камера, GPS)
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_label = QLabel("ПО — | Модель — | Сервер — | Камера — | GPS —")
        self._status_bar.addPermanentWidget(self._status_label, 0)
        self._last_status_str: Optional[str] = None  # для лога при изменении

        # Таймер обновления статуса (раз в 2 сек)
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status_bar)
        self._status_timer.start(2000)
        self._update_status_bar()

        self._update_map()

        self._stack = QStackedWidget()
        self._stack.addWidget(loading_page)
        self._stack.addWidget(main_content)
        self._stack.setCurrentIndex(0)  # сначала экран загрузки
        self.setCentralWidget(self._stack)

    def _on_camera_open_failed(self):
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_record.setEnabled(False)
        self._video_label.setText("Ошибка: не удалось открыть камеру.\nЗакройте окно Iriun Webcam на ПК и нажмите «Старт» снова.")
        self._update_status_bar()

    def _update_status_bar(self):
        running = self._camera.is_running()
        gps_ok = getattr(self._gps, "is_connected", lambda: False)()
        model_ok = self._camera.is_alpr_loaded() if running else False
        server_ok = (
            getattr(self._api, "_server_reachable", False)
            or getattr(self._api, "_last_telemetry_ok", False)
            or getattr(self._api, "_last_detection_ok", False)
        )
        camera_ok = self._camera.has_frame() if running else False

        if running:
            s_po = "ПО ✓"
            s_model = "Модель ✓" if model_ok else "Модель …"
            s_server = "Сервер ✓" if server_ok else "Сервер …"
            s_cam = "Камера ✓" if camera_ok else "Камера …"
        else:
            s_po = "ПО —"
            s_model = "Модель —"
            s_server = "Сервер —"
            s_cam = "Камера —"
        s_gps = "GPS ✓" if gps_ok else "GPS —"

        status_str = f"{s_po} | {s_model} | {s_server} | {s_cam} | {s_gps}"
        self._status_label.setText(status_str)
        if status_str != self._last_status_str:
            self._last_status_str = status_str
            logger.info("Статус: %s", status_str)

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
        self._update_status_bar()
        # Вывод видео: обновление каждые 25 ms (~40 fps), меньше лагов
        self._display_timer = QTimer(self)
        self._display_timer.timeout.connect(self._paint_latest_frame)
        self._display_timer.start(25)
        # Проверка доступности сервера в фоне — тогда в статус-баре сразу «Сервер ✓»
        threading.Thread(target=self._api.check_server, daemon=True).start()

    def _on_stop(self):
        if self._display_timer:
            self._display_timer.stop()
            self._display_timer = None
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
        self._video_label.setText("Видеопоток\nЗапустите мониторинг")
        self._update_status_bar()

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
        if plate:
            key = plate.strip().upper().replace(" ", "")
            dedup_sec = DETECTION_DEDUP_HOURS * 3600
        else:
            key = "crop:" + hashlib.sha256(crop_image).hexdigest()[:16]
            dedup_sec = CROP_DEDUP_MINUTES * 60
        now = time.time()
        if key in self._sent_plates and (now - self._sent_plates[key]) < dedup_sec:
            logger.info("[Сервер] Пропуск дубликата: уже отправлялось (ключ %s), на сервер не шлём", key[:20] + "…" if len(key) > 20 else key)
        else:
            self._sent_plates[key] = now
            logger.info("[Сервер] Обнаружен номер %s — отправляю на сервер", display_text)
            ok = self._api.send_detection(plate, full_image, crop_image)
            if not ok:
                logger.warning("[Сервер] Отправка не удалась для номера %s (см. сообщения выше)", display_text)
            plate_dedup_sec = DETECTION_DEDUP_HOURS * 3600
            crop_dedup_sec = CROP_DEDUP_MINUTES * 60
            for k in list(self._sent_plates):
                limit = crop_dedup_sec * 2 if k.startswith("crop:") else plate_dedup_sec * 2
                if now - self._sent_plates[k] > limit:
                    del self._sent_plates[k]
        pos = self._gps.get_position()
        self._camera.set_gps(pos.latitude, pos.longitude)

    def _send_telemetry(self):
        pos = self._gps.get_position()
        self._api.send_telemetry(pos)
        self._camera.set_gps(pos.latitude, pos.longitude)
        self._update_map(lat=pos.latitude, lon=pos.longitude)

    def _update_map(self, lat: float = None, lon: float = None):
        if not HAS_MAP or not folium:
            return
        if lat is None or lon is None:
            pos = self._gps.get_position()
            lat, lon = pos.latitude, pos.longitude
        try:
            m = folium.Map(location=[lat, lon], zoom_start=15)
            folium.Marker([lat, lon], popup="Патруль").add_to(m)
            html = m.get_root().render()
            if HAS_MAP and QWebEngineView is not None and hasattr(self._map_view, "setHtml"):
                self._map_view.setHtml(html)
            elif hasattr(self._map_view, "setText"):
                self._map_view.setText(f"GPS: {lat:.5f}, {lon:.5f}")
        except Exception as e:
            logger.debug("Map update error: %s", e)
