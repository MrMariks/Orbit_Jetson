# Orbit_Jetson

Патрульный модуль для устройства **Nvidia Jetson**: отдельное клиентское приложение с камерой, распознаванием номеров (ALPR nomeroff-net) и отправкой данных на **Orbit_Backend**.

## Технологии

- **Python** — основной язык
- **PyQt5** — графический интерфейс
- **folium** — карта (отображение в QWebEngineView)
- **OpenCV** — захват и обработка видео с камеры
- **nomeroff-net** — ALPR (детекция рамки номера, выравнивание, OCR под СНГ/РК)

## Структура проекта

```
orbit_jetson/
├── __init__.py
├── main.py          # Точка входа
├── ui.py            # Окно PyQt5: видео, номера, карта, кнопки
├── camera.py        # Камера + ALPR (nomeroff-net), отправка детекций
├── gps.py           # Заглушка GPS (Павлодар/Актау)
├── api_client.py    # Отправка телеметрии и детекций на бэкенд
└── config.py        # Настройки (API_TOKEN, BACKEND_URL и др.)
```

## Быстрый старт

### 1. Окружение и зависимости

```bash
cd Orbit_Jetson
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
# source venv/bin/activate

pip install -r requirements.txt
```

На **Jetson** часто уже стоят PyTorch и CUDA; при конфликтах установите пакеты по [инструкциям для Jetson](https://developer.nvidia.com/embedded/downloads).

### 2. Конфигурация

Откройте `orbit_jetson/config.py` и задайте:

- **API_TOKEN** — токен, выданный в Orbit_Backend для этого патруля.
- **BACKEND_URL** — адрес сервера (например, `http://192.168.1.100:8000` без слэша в конце).

При необходимости измените:

- `TELEMETRY_INTERVAL_SEC` — интервал отправки GPS (по умолчанию 8 с).
- `CAMERA_INDEX` — индекс камеры (0 — по умолчанию).
- `DEBUG_VIDEO_PATH` — путь к видеофайлу для теста без камеры.

### 3. Запуск

Из корня репозитория:

```bash
python -m orbit_jetson.main
```

или из папки `orbit_jetson`:

```bash
python main.py
```

В окне: нажмите **«Старт мониторинга»** — пойдёт видеопоток, распознавание номеров и отправка телеметрии на бэкенд.

## Функционал

- **Видеопоток** — отображение с камеры в реальном времени, рамки вокруг распознанных номеров.
- **Распознанные номера** — список последних 5–10 номеров с временем; каждый номер отправляется на `POST /api/v1/detections`.
- **Карта** — текущая позиция по GPS (TCP NMEA с телефона или координаты по умолчанию).
- **Телеметрия** — периодическая отправка координат на `POST /api/v1/patrol/telemetry`.

## API Backend

Клиент ожидает, что Orbit_Backend предоставляет:

- **POST /api/v1/patrol/telemetry** — тело JSON: `latitude`, `longitude`, опционально `altitude`, `speed_kmh`, `timestamp`. Заголовок: `Authorization: Bearer <API_TOKEN>`.
- **POST /api/v1/detections** — тело JSON: `plate_number`, опционально `latitude`, `longitude`, `timestamp`. Тот же заголовок авторизации.

## GPS

В `gps.py` — клиент TCP для приёма NMEA с телефона; при недоступности используются координаты по умолчанию из config.

## Лицензия

По условиям проекта Orbit.
