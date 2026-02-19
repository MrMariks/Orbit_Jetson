# -*- coding: utf-8 -*-
"""
Модуль камеры: ALPR через nomeroff-net.
Детекция рамки номера → выравнивание → OCR (СНГ/РК) → фильтр по формату и confidence.
На кадре: полигон по 4 точкам, подпись «НОМЕР KZ».
"""

import logging
import re
import sys
import tempfile
import threading
import time
import warnings
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .config import (
    ALPR_CONFIDENCE_MIN,
    ALPR_COUNTRY_CODE,
    CAMERA_INDEX,
    ALPR_DETECT_EVERY_N_FRAMES,
    ALPR_CONFIRM_CYCLES,
    ALPR_CONFIRM_SECONDS,
    ALPR_MIN_BBOX_WIDTH,
    ALPR_MIN_BBOX_HEIGHT,
    ALPR_MIN_BBOX_AREA,
    ALPR_ALLOW_EU,
)

IS_WINDOWS = sys.platform == "win32"
# На Windows виртуальные камеры (Iriun, OBS) часто лучше работают с MSMF
CAP_MSMF = getattr(cv2, "CAP_MSMF", -1)
logger = logging.getLogger(__name__)

# Форматы госномеров: 000AAA00 (цифры-буквы-цифры) или A000AAA (буква-цифры-буквы)
PLATE_FORMAT_1 = re.compile(r"^\d{3}[A-Za-zА-Яа-я]{3}\d{2}$")   # 000AAA00
PLATE_FORMAT_2 = re.compile(r"^[A-Za-zА-Яа-я]\d{3}[A-Za-zА-Яа-я]{3}$")  # A000AAA
# Допускаем пробелы/дефисы между группами
PLATE_CLEAN = re.compile(r"[\s\-]+")


def normalize_plate(text: str) -> str:
    """Очистка: только буквы и цифры, верхний регистр."""
    t = PLATE_CLEAN.sub("", text).strip().upper()
    return t


def _is_likely_garbage(text: str) -> bool:
    """Отсекает мусор: повторы (клавиатура), мало разных символов, мало цифр/букв."""
    if len(text) < 6:
        return True
    c = Counter(text)
    min_unique = 4 if len(text) >= 8 else 3
    if len(c) < min_unique:
        return True
    if max(c.values()) >= 4:
        return True
    digits = sum(1 for x in text if x.isdigit())
    letters = sum(1 for x in text if x.isalpha())
    if digits < 2 or letters < 2:
        return True
    return False


def looks_like_plate(text: str, allow_eu: bool = False) -> bool:
    """Форматы КЗ/СНГ (000AAA00, A000AAA). При allow_eu=True — также EU: 6–10 букв/цифр."""
    t = normalize_plate(text)
    if len(t) != 7 and len(t) != 8 and not (allow_eu and 6 <= len(t) <= 10):
        return False
    if PLATE_FORMAT_1.match(t) or PLATE_FORMAT_2.match(t):
        if not _is_likely_garbage(t):
            return True
    if allow_eu and 6 <= len(t) <= 10:
        if not _is_likely_garbage(t) and re.search(r"\d", t) and re.search(r"[A-Za-zА-Яа-я]", t):
            return True
    return False


def _frame_is_useful(frame) -> bool:
    """Кадр не пустой и не чёрный."""
    if frame is None or frame.size == 0:
        return False
    try:
        return float(np.mean(frame)) > 25.0
    except Exception:
        return False


# Ленивая загрузка пайплайна nomeroff-net
_alpr_pipeline = None
_alpr_loaded = False  # True после первой успешной загрузки пайплайна
_alpr_failed_logged = False


def _get_alpr_pipeline():
    """Единый пайплайн: детекция → 4 точки → выравнивание → OCR (СНГ/РК)."""
    global _alpr_pipeline, _alpr_loaded, _alpr_failed_logged
    if _alpr_pipeline is not None:
        return _alpr_pipeline
    try:
        # Убираем шум в консоли: FutureWarning torch.load, UserWarning torchvision pretrained
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=FutureWarning)
            warnings.simplefilter("ignore", category=UserWarning)
            import torch
            # PyTorch 2.6+ по умолчанию weights_only=True — чекпоинты nomeroff-net содержат свои классы
            if getattr(torch.serialization, "add_safe_globals", None):
                from nomeroff_net.tools.ocr_tools import StrLabelConverter
                import torchvision.models.efficientnet as _eff
                import torchvision.models.resnet as _resnet
                _safe = [StrLabelConverter]
                if hasattr(_eff, "efficientnet_b2"):
                    _safe.append(_eff.efficientnet_b2)
                if hasattr(_resnet, "resnet18"):
                    _safe.append(_resnet.resnet18)
                torch.serialization.add_safe_globals(_safe)
            from nomeroff_net import pipeline
            from nomeroff_net.tools import unzip
            _alpr_pipeline = pipeline("number_plate_detection_and_reading", image_loader="opencv")
        global _alpr_loaded
        _alpr_loaded = True
        logger.debug("ALPR включён (nomeroff-net)")
        return _alpr_pipeline
    except OSError as e:
        if not _alpr_failed_logged:
            _alpr_failed_logged = True
            if "1114" in str(e) or "c10.dll" in str(e).lower():
                logger.warning(
                    "nomeroff-net недоступен (ошибка загрузки PyTorch DLL): %s. "
                    "Попробуйте: 1) импорт torch до Qt уже выполнен в main.py; "
                    "2) установите Visual C++ Redistributable (x64); 3) переустановите PyTorch (CPU: pip install torch --force-reinstall). Видео без распознавания.",
                    e,
                )
            else:
                logger.warning("nomeroff-net недоступен, распознавание номеров отключено: %s", e)
        return None
    except ModuleNotFoundError as e:
        if not _alpr_failed_logged:
            _alpr_failed_logged = True
            err = str(e)
            if "pkg_resources" in err:
                logger.warning(
                    "nomeroff-net недоступен: нет модуля pkg_resources. "
                    "Установите setuptools: pip install \"setuptools>=65,<82\". Видео без распознавания."
                )
            elif "turbojpeg" in err:
                logger.warning(
                    "nomeroff-net недоступен: нет модуля turbojpeg. "
                    "Установите: pip install PyTurboJPEG. Видео без распознавания."
                )
            else:
                logger.warning("nomeroff-net недоступен, распознавание номеров отключено: %s", e)
        return None
    except Exception as e:
        if not _alpr_failed_logged:
            _alpr_failed_logged = True
            logger.warning("nomeroff-net недоступен, распознавание номеров отключено: %s", e)
        return None


class CameraWorker:
    """
    Захват видео, ALPR (nomeroff-net): рамка номера → полигон → OCR → фильтр.
    Результат передаётся в api_client по тому же принципу.
    """

    def __init__(
        self,
        camera_index: int = 0,
        on_plate_detected: Optional[Callable[[str, Optional[float], Optional[float], bytes, bytes], None]] = None,
    ):
        self.camera_index = camera_index
        self.on_plate_detected = on_plate_detected
        self._cap: Optional[cv2.VideoCapture] = None
        self._running = False
        self._last_lat: Optional[float] = None
        self._last_lon: Optional[float] = None
        self._last_detected: set = set()
        self._last_dedup_time: float = 0
        self._dedup_seconds = 3.0
        self._frame_count = 0
        self._detect_every_n = ALPR_DETECT_EVERY_N_FRAMES
        self._conf_min = ALPR_CONFIDENCE_MIN
        self._country = ALPR_COUNTRY_CODE
        self._first_frame_logged = False
        self._used_camera_index: Optional[int] = None
        # Подтверждение в N кадрах: номер в список только после нескольких циклов детекции
        self._pending_plates: Dict[str, Tuple[int, float]] = {}  # key -> (frame_count, first_time)
        self._confirm_seconds = ALPR_CONFIRM_SECONDS
        self._confirm_min_frames = ALPR_CONFIRM_CYCLES * ALPR_DETECT_EVERY_N_FRAMES
        self._min_bbox_w = ALPR_MIN_BBOX_WIDTH
        self._min_bbox_h = ALPR_MIN_BBOX_HEIGHT
        self._min_bbox_area = ALPR_MIN_BBOX_AREA
        self._allow_eu = ALPR_ALLOW_EU
        self._last_raw_frame: Optional["np.ndarray"] = None
        self._last_raw_frame_lock = threading.Lock()
        self._alpr_thread: Optional[threading.Thread] = None
        self._alpr_stop = False
        self._alpr_cycle_count = 0

    def set_gps(self, lat: Optional[float], lon: Optional[float]) -> None:
        self._last_lat, self._last_lon = lat, lon

    def is_alpr_loaded(self) -> bool:
        """Модель распознавания номеров загружена."""
        return _alpr_loaded

    def has_frame(self) -> bool:
        """Получен хотя бы один кадр с камеры."""
        return getattr(self, "_first_frame_logged", False)

    def _open_source(self) -> bool:
        backends = [cv2.CAP_DSHOW]
        if IS_WINDOWS and CAP_MSMF >= 0:
            backends.append(CAP_MSMF)
        self._cap = None
        for backend in backends if IS_WINDOWS else [cv2.CAP_ANY]:
            self._cap = cv2.VideoCapture(self.camera_index, backend) if IS_WINDOWS else cv2.VideoCapture(self.camera_index)
            if self._cap and self._cap.isOpened():
                break
            if self._cap:
                self._cap.release()
                self._cap = None
        if not self._cap and not IS_WINDOWS:
            self._cap = cv2.VideoCapture(self.camera_index)
        if not self._cap or not self._cap.isOpened():
            if IS_WINDOWS:
                for idx in (0, 1, 2):
                    if idx == self.camera_index:
                        continue
                    for backend in (cv2.CAP_DSHOW, CAP_MSMF):
                        if backend < 0:
                            continue
                        cap = cv2.VideoCapture(idx, backend)
                        if cap and cap.isOpened():
                            self._cap = cap
                            self._used_camera_index = idx
                            logger.debug("Камера открыта по индексу %s (backend %s)", idx, "MSMF" if backend == CAP_MSMF else "DSHOW")
                            return True
                        if cap:
                            cap.release()
            logger.error("Не удалось открыть камеру. Индекс=%s", self.camera_index)
            return False
        self._used_camera_index = self.camera_index
        logger.debug("Камера: открыта (индекс %s)", self._used_camera_index)
        return True

    def _open_and_get_useful_frame(self, index: int, accept_any_frame: bool = False):
        """Открыть камеру по индексу; на Windows — DSHOW, затем MSMF. Если accept_any_frame=True (индекс из конфига), принимаем первый же кадр — для потока с телефона (Iriun)."""
        backends = []
        if IS_WINDOWS:
            backends = [cv2.CAP_DSHOW]
            if CAP_MSMF >= 0:
                backends.append(CAP_MSMF)
        for backend in backends if backends else [cv2.CAP_ANY]:
            cap = cv2.VideoCapture(index, backend) if backends else cv2.VideoCapture(index)
            if not cap or not cap.isOpened():
                if cap:
                    cap.release()
                continue
            time.sleep(0.5 if accept_any_frame else 0.7)
            if accept_any_frame:
                # Источник из конфига (телефон/Iriun): принимаем первый же читаемый кадр, не ждём «не чёрный»
                for _ in range(80):
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        return cap
                    time.sleep(0.05)
            else:
                for _ in range(50):
                    ret, frame = cap.read()
                    if ret and _frame_is_useful(frame):
                        return cap
                    time.sleep(0.05)
                for _ in range(30):
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        return cap
                    time.sleep(0.1)
            cap.release()
        return None

    def _try_alternative_camera(self) -> bool:
        if not IS_WINDOWS:
            return False
        for idx in (0, 1, 2):
            if idx == self._used_camera_index:
                continue
            cap = self._open_and_get_useful_frame(idx)
            if cap is not None:
                if self._cap:
                    self._cap.release()
                self._cap = cap
                self._used_camera_index = idx
                logger.debug("Камера: переключена на индекс %s", idx)
                return True
        return False

    def start(self) -> bool:
        if self._running:
            return True
        # Загрузка ALPR при старте мониторинга (ошибки — сразу в лог, не при первом кадре)
        _get_alpr_pipeline()
        if IS_WINDOWS:
            # Сначала пробуем индекс из конфига (например 1 = Iriun/телефон), потом 0, 2 — чтобы на ноутбуке брать поток с телефона, а не встроенную вебку
            order = [self.camera_index] + [i for i in (0, 1, 2) if i != self.camera_index]
            for idx in order:
                prefer = idx == self.camera_index
                cap = self._open_and_get_useful_frame(idx, accept_any_frame=prefer)
                if cap is not None:
                    self._cap = cap
                    self._used_camera_index = idx
                    self._running = True
                    self._last_detected.clear()
                    self._pending_plates.clear()
                    self._last_dedup_time = time.time()
                    self._first_frame_logged = False
                    self._start_alpr_thread()
                    logger.info("Камера: используется индекс %s%s", idx, " (источник из конфига, например телефон)" if prefer else "")
                    return True
            logger.warning("Камера: по индексам 0, 1, 2 нет изображения. Открываю индекс %s.", self.camera_index)
        if not self._open_source():
            return False
        self._running = True
        self._start_alpr_thread()
        self._last_detected.clear()
        self._pending_plates.clear()
        self._last_dedup_time = time.time()
        self._first_frame_logged = False
        if IS_WINDOWS and self._cap:
            time.sleep(0.5)
            got_useful = False
            for _ in range(40):
                ret, frame = self._cap.read()
                if ret and _frame_is_useful(frame):
                    got_useful = True
                    break
                time.sleep(0.05)
            if not got_useful and self._try_alternative_camera():
                pass
            elif not got_useful:
                logger.warning("Камера: только чёрные кадры. Включите передачу с телефона.")
        return True

    def _start_alpr_thread(self) -> None:
        if self._alpr_thread is not None:
            return
        self._alpr_stop = False
        def _alpr_loop() -> None:
            while not self._alpr_stop and self._running:
                self.run_alpr_on_latest_frame()
                time.sleep(0.5)
        self._alpr_thread = threading.Thread(target=_alpr_loop, daemon=True)
        self._alpr_thread.start()

    def run_alpr_on_latest_frame(self) -> None:
        with self._last_raw_frame_lock:
            frame = self._last_raw_frame.copy() if self._last_raw_frame is not None else None
        if frame is not None:
            self._run_alpr_on_frame(frame)

    def _run_alpr_on_frame(self, frame: "np.ndarray") -> None:
        """Запуск ALPR в отдельном потоке — не блокирует показ картинки."""
        self._alpr_cycle_count += 1
        now = time.time()
        pipeline_fn = _get_alpr_pipeline()
        if pipeline_fn is None:
            return
        try:
            from nomeroff_net.tools import unzip
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                cv2.imwrite(f.name, frame)
                path = f.name
            try:
                result = pipeline_fn([path])
                unpacked = unzip(result)
                if len(unpacked) >= 9:
                    images_bboxs, images_points, region_names, confidences, texts = (
                        unpacked[1], unpacked[2], unpacked[5], unpacked[7], unpacked[8]
                    )
                else:
                    images_points, texts = ([], []), []
                    confidences, region_names = [], []
                points_list = (images_points[0] if images_points and len(images_points) > 0 else [])
                texts_list = list(texts[0]) if texts and len(texts) > 0 else []
                conf_list = list(confidences[0]) if confidences and len(confidences) > 0 else []
                regions_list = list(region_names[0]) if region_names and len(region_names) > 0 else []
                passed = 0
                for i, pts in enumerate(points_list):
                    if pts is None or len(pts) < 4:
                        continue
                    raw_conf = conf_list[i] if i < len(conf_list) else 0.0
                    conf = float(min(raw_conf)) if isinstance(raw_conf, (list, tuple)) and raw_conf else float(raw_conf)
                    if conf < self._conf_min:
                        continue
                    raw_text = texts_list[i] if i < len(texts_list) else ""
                    text = "".join(str(x) for x in raw_text) if isinstance(raw_text, (list, tuple)) else str(raw_text)
                    text = normalize_plate(text.strip())
                    if not text or not looks_like_plate(text, self._allow_eu):
                        continue
                    points_xy = [(int(pts[j][0]), int(pts[j][1])) for j in range(min(4, len(pts)))]
                    xs, ys = [p[0] for p in points_xy], [p[1] for p in points_xy]
                    w, h = max(xs) - min(xs), max(ys) - min(ys)
                    if h <= 0 or w <= 0:
                        continue
                    if w / h < 2.2 or w / h > 6.5:
                        continue
                    if w < self._min_bbox_w or h < self._min_bbox_h or (w * h) < self._min_bbox_area:
                        continue
                    passed += 1
                    raw_region = regions_list[i] if i < len(regions_list) else self._country
                    region = str(raw_region[0] if isinstance(raw_region, (list, tuple)) and raw_region else raw_region) if raw_region else self._country
                    key = normalize_plate(text)
                    if key not in self._pending_plates:
                        self._pending_plates[key] = (self._alpr_cycle_count, now)
                    else:
                        first_cycle, first_time = self._pending_plates[key]
                        if now - first_time > self._confirm_seconds:
                            self._pending_plates[key] = (self._alpr_cycle_count, now)
                        elif self._alpr_cycle_count - first_cycle >= ALPR_CONFIRM_CYCLES:
                            if self._dedup_and_emit(text) and self.on_plate_detected:
                                zoomed_img = self._crop_zoomed_region(frame, points_xy, expand_factor=2.0)
                                zoomed_image_bytes = (cv2.imencode(".jpg", zoomed_img, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tobytes() if zoomed_img is not None else cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes())
                                crop_img = self._crop_plate_region(frame, points_xy)
                                crop_image_bytes = cv2.imencode(".jpg", crop_img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes() if crop_img is not None else zoomed_image_bytes
                                self.on_plate_detected(text, self._last_lat, self._last_lon, zoomed_image_bytes, crop_image_bytes)
                            del self._pending_plates[key]
                for k in list(self._pending_plates):
                    if now - self._pending_plates[k][1] > self._confirm_seconds + 1:
                        del self._pending_plates[k]
            finally:
                Path(path).unlink(missing_ok=True)
        except Exception as e:
            logger.warning("ALPR при разборе кадра: %s", e)

    def stop(self) -> None:
        self._running = False
        self._alpr_stop = True
        if self._alpr_thread is not None:
            self._alpr_thread.join(timeout=5.0)
            self._alpr_thread = None
        if self._cap:
            self._cap.release()
            self._cap = None

    def is_running(self) -> bool:
        return self._running

    def _dedup_and_emit(self, text: str) -> bool:
        now = time.time()
        if now - self._last_dedup_time > self._dedup_seconds:
            self._last_detected.clear()
            self._last_dedup_time = now
        key = normalize_plate(text)
        if key in self._last_detected:
            return False
        self._last_detected.add(key)
        return True

    @staticmethod
    def _crop_plate_region(frame: "np.ndarray", points_xy: List[Tuple[int, int]]) -> Optional["np.ndarray"]:
        """Вырезает прямоугольную область номера по bounding box точек. Возвращает None при некорректных границах."""
        if not points_xy or frame is None:
            return None
        xs = [p[0] for p in points_xy]
        ys = [p[1] for p in points_xy]
        h, w = frame.shape[:2]
        x1 = max(0, min(xs))
        y1 = max(0, min(ys))
        x2 = min(w, max(xs) + 1)
        y2 = min(h, max(ys) + 1)
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2].copy()

    @staticmethod
    def _crop_zoomed_region(
        frame: "np.ndarray", points_xy: List[Tuple[int, int]], expand_factor: float = 2.0
    ) -> Optional["np.ndarray"]:
        """Вырезает область вокруг номера с отступом (зум на номер). expand_factor — во сколько раз расширить bbox по сторонам (2 = по 50% с каждой стороны)."""
        if not points_xy or frame is None:
            return None
        xs = [p[0] for p in points_xy]
        ys = [p[1] for p in points_xy]
        h, w = frame.shape[:2]
        cx = (min(xs) + max(xs)) / 2
        cy = (min(ys) + max(ys)) / 2
        bw = max(xs) - min(xs) + 1
        bh = max(ys) - min(ys) + 1
        pad_w = bw * (expand_factor - 1) / 2
        pad_h = bh * (expand_factor - 1) / 2
        x1 = int(max(0, cx - bw / 2 - pad_w))
        y1 = int(max(0, cy - bh / 2 - pad_h))
        x2 = int(min(w, cx + bw / 2 + pad_w))
        y2 = int(min(h, cy + bh / 2 + pad_h))
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2].copy()

    def _draw_plate_polygon(self, frame, points: List[Tuple[int, int]], text: str, region: str) -> None:
        """Отрисовка: полигон по 4 точкам, над ним подпись «НОМЕР KZ»."""
        pts = np.array(points, dtype=np.int32)
        cv2.polylines(frame, [pts], True, (0, 255, 0), 3)
        label = f"{text} {region}".strip()
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2)
        x0 = int(min(p[0] for p in points))
        y0 = int(min(p[1] for p in points))
        tx, ty = x0, y0 - 6
        if ty - th < 0:
            ty = int(max(p[1] for p in points)) + th + 4
        cv2.rectangle(frame, (tx, ty - th), (tx + tw + 6, ty + 4), (0, 255, 0), -1)
        cv2.putText(frame, label, (tx + 2, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 0), 2)

    def read_frame(self) -> Tuple[bool, Optional[bytes]]:
        """
        Читает кадр и сразу отдаёт для показа — без блокировки на ALPR (распознавание в отдельном потоке).
        """
        if not self._running or not self._cap:
            return False, None
        ret, frame = self._cap.read()
        if not ret or frame is None:
            return False, None
        with self._last_raw_frame_lock:
            self._last_raw_frame = frame.copy()
        self._frame_count += 1
        if not self._first_frame_logged:
            self._first_frame_logged = True
            logger.debug("Камера: OK, изображение получено")
        h, w = frame.shape[:2]
        max_w = 960
        jpeg_quality = 75
        if w > max_w:
            scale = max_w / w
            frame = cv2.resize(frame, (max_w, int(h * scale)), interpolation=cv2.INTER_LINEAR)
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        return True, buf.tobytes()

    def read_frame_raw(self) -> Tuple[bool, Optional["cv2.Mat"]]:
        if not self._running or not self._cap:
            return False, None
        ret, frame = self._cap.read()
        if not ret or frame is None:
            return False, None
        return True, frame
