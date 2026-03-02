# -*- coding: utf-8 -*-
"""
Пакет GPS: приём координат только через браузер (HTTP).
На телефоне откройте http://IP_ПК:8765/ и дайте доступ к геолокации.

Использование: from orbit_jetson.gps import get_gps, GpsPosition
"""

from .client import (
    GpsPosition,
    BrowserGps,
    get_gps,
)

__all__ = ["GpsPosition", "BrowserGps", "get_gps"]
