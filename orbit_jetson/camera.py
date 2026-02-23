# -*- coding: utf-8 -*-
"""
Модуль камеры: ALPR через nomeroff-net.
Детекция рамки номера → выравнивание → OCR (СНГ/РК) → фильтр по формату и confidence.
На кадре: полигон по 4 точкам, подпись «НОМЕР KZ».
"""

import logging
import queue
import re
import sys
import tempfile
import threading
import time
import warnings
from collections import Counter
from datetime import datetime
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
    ALPR_HIGH_CONF_ADD_AT_ONCE,
    DRAW_OBJECT_BOXES,
    SEND_DETECTION_ONLY,
    ALPR_DEFAULT_LABEL,
    ALPR_UPSCALING,
    ALPR_JPEG_QUALITY,
    ALPR_INPUT_MIN_WIDTH,
    ALPR_PRE_SHARPEN,
    ALPR_FULL_EVERY_N_CYCLES,
    ALPR_DETECT_THEN_OCR_ASYNC,
    ALPR_CROP_UPSCALE_FOR_OCR,
    DISPLAY_WIDTH,
    DISPLAY_HEIGHT,
    DISPLAY_JPEG_QUALITY,
    RECORDING_DIR,
    RECORDING_FPS,
)

IS_WINDOWS = sys.platform == "win32"
# На Windows виртуальные камеры (Iriun, OBS) часто лучше работают с MSMF
CAP_MSMF = getattr(cv2, "CAP_MSMF", -1)
# Скрыть предупреждения OpenCV «can't be used to capture by index» при переборе камер (всё равно пробуем следующий индекс)
if getattr(cv2, "setLogLevel", None) is not None:
    cv2.setLogLevel(3)  # 3 = LOG_LEVEL_ERROR, без WARNING
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


def _fix_plate_ocr(text: str) -> str:
    """Исправление типичных ошибок OCR: O→0, I→1 в позициях цифр (чтобы номера вроде 024AJM14 не терялись)."""
    if len(text) != 7 and len(text) != 8:
        return text
    s = list(text)
    if len(s) == 8:
        for i in (0, 1, 2, 6, 7):
            if s[i] == "O":
                s[i] = "0"
            elif s[i] == "I":
                s[i] = "1"
    else:
        for i in (1, 2, 3):
            if i < len(s) and s[i] == "O":
                s[i] = "0"
            elif i < len(s) and s[i] == "I":
                s[i] = "1"
    return "".join(s)


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


# Ленивая загрузка пайплайнов nomeroff-net
_alpr_pipeline = None
_detect_only_pipeline = None  # только детекция (рамки), без OCR — быстрее
_alpr_loaded = False  # True после первой успешной загрузки пайплайна
_alpr_failed_logged = False


def _get_detect_only_pipeline():
    """Пайплайн только детекции (YOLO + точки). Используется для отрисовки рамок без OCR, чтобы OCR не мешал находить номера."""
    global _detect_only_pipeline
    if _detect_only_pipeline is not None:
        return _detect_only_pipeline
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=FutureWarning)
            warnings.simplefilter("ignore", category=UserWarning)
            import torch
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
            _detect_only_pipeline = pipeline("number_plate_localization", image_loader="opencv")
        return _detect_only_pipeline
    except Exception:
        return None


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
            # Регион/пресет для OCR (как в официальном репо: https://github.com/ria-com/nomeroff-net)
            default_label = (ALPR_DEFAULT_LABEL or "").strip() or {
                "KZ": "kz",
                "RU": "ru",
                "UA": "eu_ua_2015",
            }.get((ALPR_COUNTRY_CODE or "KZ").upper(), "eu_ua_2015")
            _alpr_pipeline = pipeline(
                "number_plate_detection_and_reading",
                image_loader="opencv",
                default_label=default_label,
                upscaling=ALPR_UPSCALING,
            )
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


def preload_alpr() -> None:
    """Предзагрузка модели при запуске приложения. По нажатию «Старт мониторинга» сканирование начнётся сразу."""
    if _get_alpr_pipeline() is not None:
        logger.info("ALPR: модель готова, по нажатию «Старт мониторинга» сканирование начнётся сразу")


def warmup_camera() -> None:
    """
    Коротко открыть и закрыть камеру по индексам 0,1,2 — «прогрев» драйвера.
    После этого при нажатии «Старт мониторинга» камера часто открывается сразу, без зависания.
    """
    from .config import CAMERA_INDEX
    order = [CAMERA_INDEX] + [i for i in (0, 1, 2) if i != CAMERA_INDEX]
    for idx in order:
        try:
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW) if IS_WINDOWS else cv2.VideoCapture(idx)
            if cap and cap.isOpened():
                cap.read()
                cap.release()
                logger.debug("Камера: прогрев индекса %s", idx)
                break
        except Exception:
            pass


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
        self._last_draw_bboxes: List[Tuple[int, int, int, int]] = []
        self._last_draw_plate_points: List[List[Tuple[int, int]]] = []
        self._last_draw_lock = threading.Lock()
        self._alpr_thread: Optional[threading.Thread] = None
        self._alpr_stop = False
        self._alpr_cycle_count = 0
        self._alpr_no_frame_count = 0
        self._alpr_dropped_conf_count = 0
        self._recording = False
        self._video_writer: Optional["cv2.VideoWriter"] = None
        self._recording_path: Optional[str] = None
        self._recording_lock = threading.Lock()
        self._last_sent_regions: Dict[Tuple[int, int], float] = {}  # (cx//80, cy//80) -> time
        self._region_dedup_seconds = 10.0
        self._ocr_queue: "queue.Queue" = queue.Queue(maxsize=10)  # (crop_np, full_np, lat, lon) для фонового OCR
        self._ocr_thread: Optional[threading.Thread] = None
        self._ocr_stop = False

    def set_gps(self, lat: Optional[float], lon: Optional[float]) -> None:
        self._last_lat, self._last_lon = lat, lon

    def is_alpr_loaded(self) -> bool:
        """Модель распознавания номеров загружена."""
        return _alpr_loaded

    def has_frame(self) -> bool:
        """Получен хотя бы один кадр с камеры."""
        return getattr(self, "_first_frame_logged", False)

    def is_recording(self) -> bool:
        """Идёт ли запись видео (то, что видно в окне, с рамками на номерах)."""
        with self._recording_lock:
            return self._recording and self._video_writer is not None

    def start_recording(self) -> Optional[str]:
        """
        Начать запись: сохраняется то же изображение, что в окне (с рамками на номерах).
        Возвращает путь к файлу или None при ошибке.
        """
        with self._recording_lock:
            if self._video_writer is not None:
                return self._recording_path
            dw = max(320, int(DISPLAY_WIDTH))
            dh = max(240, int(DISPLAY_HEIGHT))
            fps = max(1, min(30, int(RECORDING_FPS)))
            rec_dir = Path(RECORDING_DIR or "recordings")
            rec_dir.mkdir(parents=True, exist_ok=True)
            name = f"orbit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.avi"
            path = str(rec_dir / name)
            fourcc = cv2.VideoWriter_fourcc(*"XVID")
            writer = cv2.VideoWriter(path, fourcc, fps, (dw, dh))
            if not writer.isOpened():
                logger.warning("Запись: не удалось открыть %s", path)
                return None
            self._video_writer = writer
            self._recording_path = path
            self._recording = True
            logger.info("Запись начата: %s", path)
            return path

    def stop_recording(self) -> Optional[str]:
        """Остановить запись. Возвращает путь к сохранённому файлу."""
        with self._recording_lock:
            path = self._recording_path
            if self._video_writer is not None:
                try:
                    self._video_writer.release()
                except Exception:
                    pass
                self._video_writer = None
            self._recording = False
            self._recording_path = None
            if path:
                logger.info("Запись остановлена: %s", path)
            return path

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
            # Запросить 1920x1080 у камеры (Iriun и многие вебкамеры поддерживают) — тогда картинка не «мыльная»
            w, h = max(320, int(DISPLAY_WIDTH)), max(240, int(DISPLAY_HEIGHT))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            # Короткая пауза, чтобы устройство успело отдать первый кадр
            time.sleep(0.15 if accept_any_frame else 0.35)
            if accept_any_frame:
                # Источник из конфига (телефон/Iriun): принимаем первый же читаемый кадр
                for _ in range(100):
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        return cap
                    time.sleep(0.02)  # чаще опрашиваем — картинка появится быстрее
            else:
                for _ in range(40):
                    ret, frame = cap.read()
                    if ret and _frame_is_useful(frame):
                        return cap
                    time.sleep(0.03)
                for _ in range(25):
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        return cap
                    time.sleep(0.05)
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
        if not SEND_DETECTION_ONLY:
            _get_detect_only_pipeline()
        if ALPR_DETECT_THEN_OCR_ASYNC and not SEND_DETECTION_ONLY:
            self._ocr_stop = False
            self._ocr_thread = threading.Thread(target=self._ocr_worker_loop, daemon=True)
            self._ocr_thread.start()
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
            time.sleep(0.2)
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
        if frame is None:
            self._alpr_no_frame_count += 1
            if self._alpr_no_frame_count % 10 == 1:
                logger.info("ALPR: кадр от камеры не приходит (ожидание %s). Проверьте: камера открыта? Картинка в окне есть?", self._alpr_no_frame_count)
                sys.stdout.flush()
                sys.stderr.flush()
            return
        self._alpr_no_frame_count = 0
        if not getattr(self, "_alpr_first_frame_logged", False):
            self._alpr_first_frame_logged = True
            logger.info("ALPR: кадры с камеры приходят, запуск распознавания…")
            sys.stdout.flush()
            sys.stderr.flush()
        self._run_alpr_on_frame(frame)

    def _run_alpr_on_frame(self, frame: "np.ndarray") -> None:
        """Запуск ALPR в отдельном потоке — не блокирует показ картинки."""
        self._alpr_cycle_count += 1
        now = time.time()
        pipeline_fn = _get_alpr_pipeline()
        if pipeline_fn is None:
            if self._alpr_cycle_count % 20 == 1:
                logger.warning("ALPR: модель не загружена (nomeroff-net/PyTorch). Проверьте установку: pip install -r requirements.txt")
            return
        try:
            from nomeroff_net.tools import unzip
            if self._alpr_cycle_count == 1:
                try:
                    import torch
                    on_gpu = torch.cuda.is_available()
                except Exception:
                    on_gpu = False
                if on_gpu:
                    logger.info("ALPR: запуск модели на первом кадре (GPU), подождите…")
                else:
                    logger.info("ALPR: запуск модели на первом кадре (CPU, может занять 1–2 мин), подождите…")
                sys.stdout.flush()
                sys.stderr.flush()
            h_orig, w_orig = frame.shape[:2]
            alpr_frame = frame
            scale_to_orig_x = 1.0
            scale_to_orig_y = 1.0
            min_w = max(0, int(ALPR_INPUT_MIN_WIDTH))
            if min_w > 0 and w_orig < min_w:
                scale = min_w / float(w_orig)
                w_alpr = min_w
                h_alpr = int(round(h_orig * scale))
                alpr_frame = cv2.resize(frame, (w_alpr, h_alpr), interpolation=cv2.INTER_LINEAR)
                scale_to_orig_x = w_orig / float(w_alpr)
                scale_to_orig_y = h_orig / float(h_alpr)
            if ALPR_PRE_SHARPEN:
                kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
                alpr_frame = cv2.filter2D(alpr_frame, -1, kernel)
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                q = max(1, min(100, int(ALPR_JPEG_QUALITY)))
                cv2.imwrite(f.name, alpr_frame, [cv2.IMWRITE_JPEG_QUALITY, q])
                path = f.name
            try:
                # При ALPR_DETECT_THEN_OCR_ASYNC=False всегда полный пайплайн (нашли рамку и сразу распознали). При True — только детекция, OCR в фоне.
                run_full = SEND_DETECTION_ONLY or not ALPR_DETECT_THEN_OCR_ASYNC or (self._alpr_cycle_count % ALPR_FULL_EVERY_N_CYCLES == 1)
                if not run_full:
                    detect_only_fn = _get_detect_only_pipeline()
                    if detect_only_fn is not None:
                        result_det = detect_only_fn([path])
                        if result_det and len(result_det) > 0:
                            out = result_det[0]
                            # number_plate_localization: result[0] = (detections_for_image, image); для одного кадра — одна пара
                            if isinstance(out, (list, tuple)) and len(out) >= 1:
                                detections = out[0] if isinstance(out[0], list) else []
                                draw_bboxes_d: List[Tuple[int, int, int, int]] = []
                                draw_plate_points_d: List[List[Tuple[int, int]]] = []
                                for det in detections:
                                    if not hasattr(det, "__len__") or len(det) < 7:
                                        continue
                                    x1, y1, x2, y2 = int(float(det[0])), int(float(det[1])), int(float(det[2])), int(float(det[3]))
                                    conf_d = float(det[4]) if len(det) > 4 else 0.0
                                    pts = det[6]
                                    if pts is not None and len(pts) >= 4:
                                        points_xy = [(int(float(pts[j][0])), int(float(pts[j][1]))) for j in range(min(4, len(pts)))]
                                        if len(points_xy) == 4:
                                            xs, ys = [p[0] for p in points_xy], [p[1] for p in points_xy]
                                            w, h = max(xs) - min(xs), max(ys) - min(ys)
                                            points_xy_orig = [(int(x * scale_to_orig_x), int(y * scale_to_orig_y)) for x, y in points_xy] if (scale_to_orig_x != 1.0 or scale_to_orig_y != 1.0) else points_xy
                                            draw_bboxes_d.append((x1, y1, x2, y2))
                                            draw_plate_points_d.append(points_xy)
                                            if ALPR_DETECT_THEN_OCR_ASYNC and conf_d >= self._conf_min and h > 0 and w > 0 and 2.2 <= w / h <= 6.5 and w >= self._min_bbox_w and h >= self._min_bbox_h and (w * h) >= self._min_bbox_area:
                                                cx, cy = (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2
                                                if self._dedup_and_emit_region(cx, cy):
                                                    crop_img = self._crop_plate_region(frame, points_xy_orig)
                                                    if crop_img is not None and not self._ocr_queue.full():
                                                        try:
                                                            self._ocr_queue.put_nowait((crop_img.copy(), frame.copy(), self._last_lat or 0.0, self._last_lon or 0.0))
                                                            logger.info(
                                                                "Найден номер (рамка), отправляю на распознавание (OCR). Центр: %s×%s, уверенность: %.2f, в очереди: %s",
                                                                cx, cy, conf_d, self._ocr_queue.qsize(),
                                                            )
                                                        except queue.Full:
                                                            pass
                                if scale_to_orig_x != 1.0 or scale_to_orig_y != 1.0:
                                    draw_bboxes_d = [(int(x1 * scale_to_orig_x), int(y1 * scale_to_orig_y), int(x2 * scale_to_orig_x), int(y2 * scale_to_orig_y)) for (x1, y1, x2, y2) in draw_bboxes_d]
                                    draw_plate_points_d = [[(int(x * scale_to_orig_x), int(y * scale_to_orig_y)) for x, y in pts] for pts in draw_plate_points_d]
                                with self._last_draw_lock:
                                    self._last_draw_bboxes = draw_bboxes_d
                                    self._last_draw_plate_points = draw_plate_points_d
                        try:
                            Path(path).unlink(missing_ok=True)
                        except Exception:
                            pass
                        return
                result = pipeline_fn([path])
                unpacked = unzip(result)
                if len(unpacked) >= 9:
                    images_bboxs, images_points, region_names, confidences, texts = (
                        unpacked[1], unpacked[2], unpacked[5], unpacked[7], unpacked[8]
                    )
                else:
                    images_bboxs, images_points, texts = [], ([], []), []
                    confidences, region_names = [], []
                points_list = (images_points[0] if images_points and len(images_points) > 0 else [])
                texts_list = list(texts[0]) if texts and len(texts) > 0 else []
                conf_list = list(confidences[0]) if confidences and len(confidences) > 0 else []
                regions_list = list(region_names[0]) if region_names and len(region_names) > 0 else []
                # Боксы объектов (детекция YOLO) и полигоны номеров для отрисовки в видео
                draw_bboxes: List[Tuple[int, int, int, int]] = []
                if images_bboxs and len(images_bboxs) > 0:
                    bbox_list = images_bboxs[0] if isinstance(images_bboxs[0], (list, tuple)) else []
                    for box in bbox_list:
                        try:
                            if not hasattr(box, "__len__") or len(box) < 4:
                                continue
                            if isinstance(box[0], (list, tuple, np.ndarray)) or (hasattr(box[0], "__len__") and not isinstance(box[0], (int, float))):
                                xs = [int(float(box[j][0])) for j in range(min(4, len(box)))]
                                ys = [int(float(box[j][1])) for j in range(min(4, len(box)))]
                            else:
                                xs = [int(float(box[0])), int(float(box[2]))]
                                ys = [int(float(box[1])), int(float(box[3]))]
                            if len(xs) >= 2 and len(ys) >= 2 and max(xs) > min(xs) and max(ys) > min(ys):
                                draw_bboxes.append((min(xs), min(ys), max(xs), max(ys)))
                        except (TypeError, ValueError, IndexError):
                            continue
                draw_plate_points: List[List[Tuple[int, int]]] = []
                for pts in points_list:
                    if pts is not None and len(pts) >= 4:
                        points_xy = [(int(pts[j][0]), int(pts[j][1])) for j in range(min(4, len(pts)))]
                        if len(points_xy) == 4:
                            draw_plate_points.append(points_xy)
                if scale_to_orig_x != 1.0 or scale_to_orig_y != 1.0:
                    draw_bboxes = [
                        (int(x1 * scale_to_orig_x), int(y1 * scale_to_orig_y),
                         int(x2 * scale_to_orig_x), int(y2 * scale_to_orig_y))
                        for (x1, y1, x2, y2) in draw_bboxes
                    ]
                    draw_plate_points = [
                        [(int(x * scale_to_orig_x), int(y * scale_to_orig_y)) for x, y in pts]
                        for pts in draw_plate_points
                    ]
                with self._last_draw_lock:
                    self._last_draw_bboxes = draw_bboxes
                    self._last_draw_plate_points = draw_plate_points
                num_raw = len(points_list)
                passed = 0
                if self._alpr_cycle_count == 1:
                    logger.info("ALPR: первый кадр обработан, в кадре детекций: %s", num_raw)
                if num_raw == 0 and self._alpr_cycle_count % 10 == 1:
                    logger.info("ALPR: модель работает, в кадре номер не найден (цикл %s). Наведите на номер или снизьте ALPR_CONFIDENCE_MIN в config.py до 0.70", self._alpr_cycle_count)
                for i, pts in enumerate(points_list):
                    if pts is None or len(pts) < 4:
                        continue
                    raw_conf = conf_list[i] if i < len(conf_list) else 0.0
                    conf = float(min(raw_conf)) if isinstance(raw_conf, (list, tuple)) and raw_conf else float(raw_conf)
                    raw_text = texts_list[i] if i < len(texts_list) else ""
                    text = "".join(str(x) for x in raw_text) if isinstance(raw_text, (list, tuple)) else str(raw_text)
                    text = normalize_plate(text.strip())
                    if conf < self._conf_min:
                        self._alpr_dropped_conf_count += 1
                        if self._alpr_dropped_conf_count % 10 == 1:
                            logger.info(
                                "ALPR: номер отброшен по уверенности (%.2f < %.2f): «%s». Снизьте ALPR_CONFIDENCE_MIN в config.py до 0.70–0.75",
                                conf, self._conf_min, text or "(пусто)",
                            )
                        continue
                    points_xy = [(int(pts[j][0]), int(pts[j][1])) for j in range(min(4, len(pts)))]
                    if scale_to_orig_x != 1.0 or scale_to_orig_y != 1.0:
                        points_xy_orig = [(int(x * scale_to_orig_x), int(y * scale_to_orig_y)) for x, y in points_xy]
                    else:
                        points_xy_orig = points_xy
                    xs, ys = [p[0] for p in points_xy], [p[1] for p in points_xy]
                    w, h = max(xs) - min(xs), max(ys) - min(ys)
                    if h <= 0 or w <= 0:
                        continue
                    if w / h < 2.2 or w / h > 6.5:
                        continue
                    if w < self._min_bbox_w or h < self._min_bbox_h or (w * h) < self._min_bbox_area:
                        continue
                    passed += 1
                    # Режим «только детекция»: отправляем рамку на сервер с пустым plate_text, OCR на сервере
                    if SEND_DETECTION_ONLY:
                        cx, cy = (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2
                        if self._dedup_and_emit_region(cx, cy) and self.on_plate_detected:
                            zoomed_img = self._crop_zoomed_region(frame, points_xy_orig, expand_factor=2.0)
                            zoomed_image_bytes = (cv2.imencode(".jpg", zoomed_img, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tobytes() if zoomed_img is not None else cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes())
                            crop_img = self._crop_plate_region(frame, points_xy_orig)
                            crop_image_bytes = cv2.imencode(".jpg", crop_img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes() if crop_img is not None else zoomed_image_bytes
                            self.on_plate_detected("", self._last_lat, self._last_lon, zoomed_image_bytes, crop_image_bytes)
                        continue
                    if not text:
                        continue
                    if not looks_like_plate(text, self._allow_eu):
                        text = _fix_plate_ocr(text)
                    if not looks_like_plate(text, self._allow_eu):
                        continue
                    raw_region = regions_list[i] if i < len(regions_list) else self._country
                    region = str(raw_region[0] if isinstance(raw_region, (list, tuple)) and raw_region else raw_region) if raw_region else self._country
                    key = normalize_plate(text)
                    high_conf = conf >= ALPR_HIGH_CONF_ADD_AT_ONCE
                    if key not in self._pending_plates:
                        if high_conf:
                            if self._dedup_and_emit(text) and self.on_plate_detected:
                                zoomed_img = self._crop_zoomed_region(frame, points_xy_orig, expand_factor=2.0)
                                zoomed_image_bytes = (cv2.imencode(".jpg", zoomed_img, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tobytes() if zoomed_img is not None else cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes())
                                crop_img = self._crop_plate_region(frame, points_xy_orig)
                                crop_image_bytes = cv2.imencode(".jpg", crop_img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes() if crop_img is not None else zoomed_image_bytes
                                self.on_plate_detected(text, self._last_lat, self._last_lon, zoomed_image_bytes, crop_image_bytes)
                        else:
                            self._pending_plates[key] = (self._alpr_cycle_count, now)
                    else:
                        first_cycle, first_time = self._pending_plates[key]
                        if now - first_time > self._confirm_seconds:
                            self._pending_plates[key] = (self._alpr_cycle_count, now)
                        elif self._alpr_cycle_count - first_cycle >= ALPR_CONFIRM_CYCLES or high_conf:
                            if self._dedup_and_emit(text) and self.on_plate_detected:
                                zoomed_img = self._crop_zoomed_region(frame, points_xy_orig, expand_factor=2.0)
                                zoomed_image_bytes = (cv2.imencode(".jpg", zoomed_img, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tobytes() if zoomed_img is not None else cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes())
                                crop_img = self._crop_plate_region(frame, points_xy_orig)
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
        self._ocr_stop = True
        if self._ocr_thread is not None:
            self._ocr_thread.join(timeout=10.0)
            self._ocr_thread = None
        self.stop_recording()
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

    def _dedup_and_emit_region(self, cx: int, cy: int) -> bool:
        """Дедуп по региону (cx//80, cy//80): не слать одну и ту же область чаще чем раз в _region_dedup_seconds."""
        now = time.time()
        to_del = [k for k, t in self._last_sent_regions.items() if now - t > self._region_dedup_seconds]
        for k in to_del:
            del self._last_sent_regions[k]
        region_key = (cx // 80, cy // 80)
        if region_key in self._last_sent_regions:
            return False
        self._last_sent_regions[region_key] = now
        return True

    def _ocr_worker_loop(self) -> None:
        """Фоновый поток: берёт кропы из очереди, запускает полный пайплайн (OCR), вызывает on_plate_detected и отправку на сервер."""
        from nomeroff_net.tools import unzip
        pipeline_fn = _get_alpr_pipeline()
        if pipeline_fn is None:
            logger.warning("ALPR OCR worker: полный пайплайн недоступен")
            return
        while not self._ocr_stop:
            try:
                crop_np, full_np, lat, lon = self._ocr_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            logger.info("Распознавание номера (OCR)… в очереди осталось: %s", self._ocr_queue.qsize())
            path = None
            try:
                ocr_crop = crop_np
                scale = max(1, int(ALPR_CROP_UPSCALE_FOR_OCR or 1))
                if scale > 1 and ocr_crop is not None:
                    h, w = ocr_crop.shape[:2]
                    ocr_crop = cv2.resize(ocr_crop, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                    q = max(1, min(100, int(ALPR_JPEG_QUALITY)))
                    cv2.imwrite(f.name, ocr_crop, [cv2.IMWRITE_JPEG_QUALITY, q])
                    path = f.name
                result = pipeline_fn([path])
                unpacked = unzip(result)
                if len(unpacked) >= 9:
                    texts = unpacked[8]
                    raw_text = texts[0][0] if texts and len(texts[0]) > 0 else ""
                    text = "".join(str(x) for x in raw_text) if isinstance(raw_text, (list, tuple)) else str(raw_text)
                    text = normalize_plate(text.strip())
                    if not looks_like_plate(text, self._allow_eu):
                        text = _fix_plate_ocr(text)
                    if looks_like_plate(text, self._allow_eu) and self.on_plate_detected and self._dedup_and_emit(text):
                        full_bytes = cv2.imencode(".jpg", full_np, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()
                        crop_bytes = cv2.imencode(".jpg", crop_np, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()
                        self.on_plate_detected(text, lat, lon, full_bytes, crop_bytes)
                        logger.info("Распознан номер: %s — отправка фото и номера на сервер", text)
                    elif text:
                        logger.info("OCR: текст не похож на номер (ожидаемый формат 000AAA00 или A000AAA): «%s» — в список не добавляю", text)
                    else:
                        logger.info("OCR: текст не распознан (пусто)")
            except Exception as e:
                logger.warning("ALPR OCR worker: %s", e)
            finally:
                if path:
                    try:
                        Path(path).unlink(missing_ok=True)
                    except Exception:
                        pass

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
            logger.info("Камера: первый кадр получен (для показа и ALPR)")
        # Отрисовка боксов детекции и рамок номеров поверх кадра
        with self._last_draw_lock:
            bboxes = list(self._last_draw_bboxes)
            plate_pts = [list(pts) for pts in self._last_draw_plate_points]
        if (DRAW_OBJECT_BOXES and bboxes) or plate_pts:
            frame = frame.copy()
            if DRAW_OBJECT_BOXES:
                for (x1, y1, x2, y2) in bboxes:
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 120, 0), 2)  # синий — бокс детектора
            for pts in plate_pts:
                if len(pts) >= 4:
                    pts_np = np.array(pts, dtype=np.int32)
                    cv2.polylines(frame, [pts_np], True, (0, 255, 0), 2)  # зелёный — госномер
        dw = max(320, int(DISPLAY_WIDTH))
        dh = max(240, int(DISPLAY_HEIGHT))
        jpeg_quality = max(50, min(100, int(DISPLAY_JPEG_QUALITY)))
        frame = cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_LINEAR)
        with self._recording_lock:
            if self._video_writer is not None:
                try:
                    self._video_writer.write(frame)
                except Exception as e:
                    logger.debug("Запись кадра: %s", e)
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        return True, buf.tobytes()

    def read_frame_raw(self) -> Tuple[bool, Optional["cv2.Mat"]]:
        if not self._running or not self._cap:
            return False, None
        ret, frame = self._cap.read()
        if not ret or frame is None:
            return False, None
        return True, frame
