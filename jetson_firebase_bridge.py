# -*- coding: utf-8 -*-
"""
Мост: читает GPS из Firebase Realtime Database и шлёт на локальный Jetson (порт 8765).
Телефон пишет координаты в Firebase → этот скрипт на ПК забирает их и отправляет в Orbit_Jetson.

Запуск:
  python jetson_firebase_bridge.py --url https://gpsjetson-default-rtdb.firebaseio.com --channel myjetson --port 8765

Требования: pip install requests
"""

import argparse
import logging
import sys
import time

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Мост Firebase → Jetson GPS (localhost:port)")
    parser.add_argument("--url", required=True, help="URL Firebase RTDB без слэша в конце")
    parser.add_argument("--channel", default="myjetson", help="Путь в БД (канал), например myjetson")
    parser.add_argument("--port", type=int, default=8765, help="Порт Jetson GPS на localhost")
    parser.add_argument("--interval", type=float, default=3.0, help="Интервал опроса Firebase (сек)")
    parser.add_argument("--auth", default="", help="Секрет/токен Firebase (если нужна авторизация)")
    args = parser.parse_args()

    url = args.url.rstrip("/")
    channel = args.channel.strip("/")
    firebase_path = f"{url}/{channel}.json"
    if args.auth:
        firebase_path += f"?auth={args.auth}"
    jetson_url = f"http://127.0.0.1:{args.port}"

    last_lat, last_lon = None, None
    logger.info("Мост запущен: Firebase %s → %s/?lat=...&lon=... (каждые %s с)", firebase_path, jetson_url, args.interval)

    while True:
        try:
            r = requests.get(firebase_path, timeout=10)
            r.raise_for_status()
            data = r.json()
            if not data or not isinstance(data, dict):
                time.sleep(args.interval)
                continue
            lat = data.get("lat") or data.get("latitude")
            lon = data.get("lon") or data.get("longitude")
            if lat is None or lon is None:
                time.sleep(args.interval)
                continue
            try:
                lat_f = float(lat)
                lon_f = float(lon)
            except (TypeError, ValueError):
                time.sleep(args.interval)
                continue
            if not (-90 <= lat_f <= 90 and -180 <= lon_f <= 180):
                time.sleep(args.interval)
                continue
            if (last_lat, last_lon) == (lat_f, lon_f):
                time.sleep(args.interval)
                continue
            last_lat, last_lon = lat_f, lon_f
            gps_url = f"{jetson_url}/?lat={lat_f}&lon={lon_f}"
            resp = requests.get(gps_url, timeout=5)
            if resp.ok:
                logger.info("GPS отправлен в Jetson: %.5f, %.5f", lat_f, lon_f)
            else:
                logger.warning("Jetson ответил %s", resp.status_code)
        except requests.RequestException as e:
            logger.warning("Сеть: %s", e)
        except Exception as e:
            logger.exception("Ошибка: %s", e)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
