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

### С другого ПК (клонирование с GitHub)

```bash
git clone https://github.com/<ваш-логин>/Orbit_Jetson.git
cd Orbit_Jetson

python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

Создайте файл с API-ключом:

- Скопируйте `orbit_jetson/api_key.txt.example` в `orbit_jetson/api_key.txt`.
- Откройте `api_key.txt` и замените содержимое на ваш ключ из Orbit_Backend (одна строка).

При необходимости отредактируйте `orbit_jetson/config.py`: **BACKEND_URL**, **CAMERA_INDEX**.

Запуск:

```bash
python -m orbit_jetson.main
```

В окне нажмите **«Старт мониторинга»**.

---

### Уже есть репозиторий локально

```bash
cd Orbit_Jetson
python -m venv venv
venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

Создайте `orbit_jetson/api_key.txt` с одной строкой — API-ключ. Запуск: `python -m orbit_jetson.main`.

### Конфигурация

- **API-ключ** — хранится в `orbit_jetson/api_key.txt` (одна строка, без пробелов). Файл не коммитится в git.
- **orbit_jetson/config.py** — BACKEND_URL, CAMERA_INDEX и др. Настройки GPS (порт браузера) — **orbit_jetson/gps/config.py**.

### Запуск

Из корня проекта:

```bash
python -m orbit_jetson.main
```

## Функционал

- **Видеопоток** — отображение с камеры в реальном времени, рамки вокруг распознанных номеров.
- **Распознанные номера** — список последних номеров; детекции (номер + фото) отправляются на `POST /api/v1/detect`. Повторы одного номера не шлются 12 часов.
- **Карта** — позиция по GPS (через браузер на телефоне: открываете страницу и даёте доступ к геолокации).

## API Backend

- **POST /api/v1/detect** — JSON: `plate_text`, `full_image`, `crop_image` (base64). Заголовок: `Authorization: Bearer <API_TOKEN>`.
- **POST /api/v1/patrol/telemetry** — JSON: `timestamp` (патруль по API-токену).

## GPS через браузер (без приложений на телефоне)

Геолокация передаётся только через браузер: на телефоне открываете страницу и даёте доступ к геолокации — координаты уходят на ПК.

1. Телефон и ПК в **одной Wi‑Fi сети**.
2. В **orbit_jetson/gps/config.py**: **GPS_HTTP_PORT = 8765** (0 = выключено). Запустите приложение на ПК.
3. Узнайте **IP ПК** в Wi‑Fi (`ipconfig` → IPv4, например `192.168.1.100`).
4. На телефоне в браузере откройте: **http://IP_ПК:8765/** (например `http://192.168.1.100:8765/`).
5. Разрешите доступ к геолокации — на странице появится «Координаты отправляются на ПК». Страницу можно не закрывать, позиция будет обновляться.

Подробнее: **orbit_jetson/gps/README.md**.

## Лицензия

По условиям проекта Orbit.
