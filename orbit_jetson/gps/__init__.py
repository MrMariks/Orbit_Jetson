# -*- coding: utf-8 -*-
"""
Пакет GPS: приём координат по TCP (NMEA с телефона) и по HTTP (браузер на телефоне).
Парсит $GPRMC и $GPGGA, возвращает lat/lon. При недоступности — координаты по умолчанию из config.

Использование: from orbit_jetson.gps import get_gps, GpsPosition
Чтобы отключить GPS — удалите папку orbit_jetson/gps/ и замените использование на заглушку (см. README в папке).
"""

from .client import (
    GpsPosition,
    NetworkGps,
    GpsAggregator,
    get_gps,
)

__all__ = ["GpsPosition", "NetworkGps", "GpsAggregator", "get_gps"]
