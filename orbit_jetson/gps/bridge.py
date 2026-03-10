# -*- coding: utf-8 -*-
"""
Мост: читает GPS из Firebase Realtime Database и шлёт GET на локальный сервер Jetson.
Ссылка на Firebase закреплена в коде. Запуск из папки gps: python3 bridge.py

Переменные окружения:
  CHANNEL_ID         — канал в Firebase (по умолчанию myjetson)
  LOCAL_PORT         — порт локального сервера (по умолчанию 8765)
  FIREBASE_AUTH_SECRET — секрет базы (Database secret) из Firebase, если приходит 401
                         или положите секрет в файл .firebase_secret в папке gps (одна строка)
  LOCAL_HTTPS         — 1 или true = слать на localhost по HTTPS (по умолчанию), 0 = HTTP
"""

import logging
import os
import sys
import time
import urllib3
from pathlib import Path

# Отключаем предупреждение о проверке SSL для localhost (самоподписанный сертификат)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import requests
except ImportError:
    print("Установите зависимость: pip install requests", file=sys.stderr)
    sys.exit(1)

# Закреплённая ссылка на Firebase RTDB. Правила: channels/$channelId с .read/.write true
FIREBASE_URL = "https://gpsjetson-default-rtdb.firebaseio.com"
FIREBASE_PATH_PREFIX = "channels"  # путь в Rules: channels/$channelId

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def _extract_lat_lon(data):
    """Из ответа Firebase достаём lat/lon: либо в корне, либо в data['last'] (как пишет приложение)."""
    if not data or not isinstance(data, dict):
        return None, None
    # Приложение пишет в channels/myjetson/last: { lat, lon, ts }
    block = data.get("last") if isinstance(data.get("last"), dict) else data
    lat = block.get("lat") or block.get("latitude")
    lon = block.get("lon") or block.get("longitude")
    return lat, lon


def main():
    channel = os.environ.get("CHANNEL_ID", "myjetson").strip().strip("/") or "myjetson"
    try:
        port = int(os.environ.get("LOCAL_PORT", "8765"))
    except ValueError:
        port = 8765
    auth_secret = os.environ.get("FIREBASE_AUTH_SECRET", "").strip()
    if not auth_secret:
        secret_file = Path(__file__).resolve().parent / ".firebase_secret"
        if secret_file.is_file():
            auth_secret = secret_file.read_text(encoding="utf-8").strip()
    use_https = os.environ.get("LOCAL_HTTPS", "1").strip().lower() not in ("0", "false", "no")
    interval = 3.0

    firebase_path = f"{FIREBASE_URL.rstrip('/')}/{FIREBASE_PATH_PREFIX}/{channel}.json"
    if auth_secret:
        firebase_path += f"?auth={auth_secret}"
    scheme = "https" if use_https else "http"
    jetson_url = f"{scheme}://127.0.0.1:{port}"

    last_lat, last_lon = None, None
    log_url = f"{FIREBASE_URL.rstrip('/')}/{FIREBASE_PATH_PREFIX}/{channel}.json" + (" (+ auth)" if auth_secret else "")
    logger.info("Мост запущен: Firebase %s → %s/?lat=...&lon=... (каждые %s с)", log_url, jetson_url, interval)

    # Диагностика при старте: проверка Firebase и подключения к Jetson
    try:
        r_fb = requests.get(firebase_path, timeout=10)
        if r_fb.status_code in (401, 403):
            logger.warning("[Диагностика] Firebase: доступ запрещён (код %s). Нужен секрет в .firebase_secret или FIREBASE_AUTH_SECRET", r_fb.status_code)
        elif r_fb.ok:
            data_fb = r_fb.json()
            lat_fb, lon_fb = _extract_lat_lon(data_fb)
            if lat_fb is not None and lon_fb is not None:
                logger.info("[Диагностика] Firebase: данные есть (lat/lon найдены)")
            else:
                logger.warning("[Диагностика] Firebase: по пути channels/%s пусто или нет lat/lon. Приложение на телефоне должно писать в этот путь (или в last: {lat, lon}).", channel)
        else:
            logger.warning("[Диагностика] Firebase: ответ %s", r_fb.status_code)
    except requests.RequestException as e:
        logger.warning("[Диагностика] Firebase: ошибка запроса — %s", e)

    try:
        r_j = requests.get(jetson_url + "/", timeout=5, verify=not use_https)
        logger.info("[Диагностика] Jetson %s:8765 — подключение OK (код %s)", "HTTPS" if use_https else "HTTP", r_j.status_code)
    except requests.RequestException as e:
        logger.warning("[Диагностика] Jetson %s:8765 — не удалось подключиться: %s. Запущен ли Orbit_Jetson? Порт 8765 занят?", "HTTPS" if use_https else "HTTP", e)

    log_empty_count = 0

    while True:
        try:
            r = requests.get(firebase_path, timeout=10)
            if r.status_code in (401, 403):
                logger.warning(
                    "Firebase: доступ запрещён (код %s). Добавьте секрет: в консоли Firebase → Настройки проекта → "
                    "Сервисные аккаунты → Секреты базы данных — скопируйте секрет и запустите мост так: "
                    "FIREBASE_AUTH_SECRET=ваш_секрет python3 bridge.py",
                    r.status_code,
                )
                time.sleep(interval)
                continue
            r.raise_for_status()
            data = r.json()
            if not data or not isinstance(data, dict):
                log_empty_count += 1
                if log_empty_count <= 3 or log_empty_count % 20 == 0:
                    logger.info("Firebase: по пути %s нет данных (null или пусто). Приложение должно писать в этот же путь: {\"lat\": число, \"lon\": число}", channel)
                time.sleep(interval)
                continue
            log_empty_count = 0
            lat, lon = _extract_lat_lon(data)
            if lat is None or lon is None:
                time.sleep(interval)
                continue
            try:
                lat_f = float(lat)
                lon_f = float(lon)
            except (TypeError, ValueError):
                time.sleep(interval)
                continue
            if not (-90 <= lat_f <= 90 and -180 <= lon_f <= 180):
                time.sleep(interval)
                continue
            if (last_lat, last_lon) == (lat_f, lon_f):
                time.sleep(interval)
                continue
            last_lat, last_lon = lat_f, lon_f
            gps_url = f"{jetson_url}/?lat={lat_f}&lon={lon_f}"
            resp = requests.get(gps_url, timeout=5, verify=not use_https)
            if resp.ok:
                logger.info("GPS отправлен в Jetson: %.5f, %.5f", lat_f, lon_f)
            else:
                logger.warning("Jetson ответил %s", resp.status_code)
        except requests.RequestException as e:
            logger.warning("Сеть: %s", e)
        except Exception as e:
            logger.exception("Ошибка: %s", e)
        time.sleep(interval)


if __name__ == "__main__":
    main()
