#!/usr/bin/env sh
# Запуск моста Firebase → Jetson из папки gps.
# Сначала запустите Orbit_Jetson (сервер на 8765).
cd "$(dirname "$0")"
exec python3 bridge.py
