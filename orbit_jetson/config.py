# -*- coding: utf-8 -*-
"""Настройки Orbit_Jetson. API-ключ — в api_key.txt (одна строка)."""
from pathlib import Path

def _load_api_key() -> str:
    path = Path(__file__).parent / "api_key.txt"
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""

API_TOKEN = _load_api_key()

BACKEND_URL = "http://localhost:8000"  # сервер без слэша в конце

TELEMETRY_INTERVAL_SEC = 8   # как часто слать телеметрию (сек)
POSITION_INTERVAL_SEC = 3    # как часто слать координаты на карту (сек)

MAX_DISPLAYED_PLATES = 10    # сколько номеров показывать в списке
DETECTION_DEDUP_HOURS = 12  # не слать один номер на сервер чаще чем раз в N часов
CROP_DEDUP_MINUTES = 60     # в режиме «только детекция»: дедуп кропа по хешу (мин)

# --- Камера ---
# 0 = встроенная вебка, 1 или 2 = виртуальная камера (Iriun с телефона)
CAMERA_INDEX = 2
DISPLAY_WIDTH = 1920
DISPLAY_HEIGHT = 1080
DISPLAY_JPEG_QUALITY = 98   # качество картинки в окне (1–100)

# --- Запись видео ---
RECORDING_DIR = "recordings"
RECORDING_FPS = 30

# --- ALPR (nomeroff-net) ---
ALPR_USE_CPU = False         # False = GPU (NVIDIA + PyTorch CUDA)
ALPR_CONFIDENCE_MIN = 0.55  # минимум уверенности; 0.55 — дальние объекты, 0.72–0.78 для жёлтых/квадратных номеров КЗ, 0.85 — только «идеальные»
ALPR_ASPECT_RATIO_MIN = 1.2  # мин. соотношение ширина/высота рамки (1.2 = квадратная рамка КЗ), было 2.2
ALPR_ASPECT_RATIO_MAX = 6.5  # макс. соотношение (прямоугольная рамка)
ALPR_SQUARE_MAX = 1.9        # если ширина/высота <= это — считаем «квадратным»: не ждём OCR, сразу шлём фото на сервер (OCR там)
ALPR_COUNTRY_CODE = "KZ"
ALPR_DEFAULT_LABEL = ""     # регион OCR: "kz", "ru", "eu" или пусто = из кода страны
ALPR_UPSCALING = False      # True = лучше мелкие номера, но требует pip install upscaler
ALPR_JPEG_QUALITY = 98      # качество кадра для распознавания (1–100)
ALPR_INPUT_MIN_WIDTH = 1920 # меньше = быстрее (1280 компромисс), 1920 = лучше мелкие номера
ALPR_ALLOW_EU = False       # только КЗ/СНГ форматы
ALPR_DETECT_EVERY_N_FRAMES = 1   # 1 = каждый кадр (макс. скорость), 2 = реже, но меньше нагрузка
ALPR_LOOP_INTERVAL_SEC = 0.2  # пауза между циклами ALPR (сек); 0.2 = быстрее реакция, 0.5 = меньше нагрузка
ALPR_EVERY_NTH_CYCLE = 3      # обрабатывать каждый N-й кадр (3 = каждый 3-й), меньше фризов
ALPR_PRE_SHARPEN = False    # лёгкая резкость перед OCR (при размытой камере)
ALPR_CROP_UPSCALE_FOR_OCR = 2    # масштаб кропа перед OCR в фоне (1 = без масштаба)
ALPR_CONFIRM_CYCLES = 1     # 1 = сразу после первого совпадения (быстрее), 2 = ждать 2 цикла
ALPR_CONFIRM_SECONDS = 0.8  # сек ожидания перед отправкой (0.8 = быстрее, было 2.5)
DRAW_OBJECT_BOXES = False  # False = только зелёные рамки номеров
PLATE_BOX_PADDING_PX = 10       # отступ рамки номера с каждой стороны (px)
PLATE_BOX_SMOOTH_FRAMES = 5     # сглаживание bbox скользящим средним за N кадров
ALPR_HIGH_CONF_ADD_AT_ONCE = 0.82  # при такой уверенности — сразу в список без ожидания (быстрее)

SEND_DETECTION_ONLY = True  # True = всегда полный пайплайн (детекция+OCR); на сервер: распознанный текст+фото или только фото, если текст не распознался
ALPR_FULL_EVERY_N_CYCLES = 1      # 1 = полный пайплайн каждый раз (быстрее реакция), 4 = реже нагрузка
ALPR_DETECT_THEN_OCR_ASYNC = False  # True = детекция в основном потоке, OCR в фоне

# Режим распознавания для UI: (value, label). value "full" = Детекция+OCR, "detection_only" = только детекция.
RECOGNITION_MODES = [
    ("full", "Детекция + OCR"),
    ("detection_only", "Только детекция"),
]
# Файл для сохранения выбранного режима между сессиями (в папке пакета)
RECOGNITION_MODE_PERSIST_FILE = Path(__file__).parent / ".recognition_mode"
