# -*- coding: utf-8 -*-
"""
Модуль GPS: клиент TCP для приёма NMEA с телефона.
Парсит $GPRMC и $GPGGA, возвращает lat/lon. При недоступности телефона
возвращает последние известные или координаты по умолчанию из config.
"""

import logging
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from .config import DEFAULT_LAT, DEFAULT_LON, GPS_PORT, PHONE_IP

logger = logging.getLogger(__name__)


@dataclass
class GpsPosition:
    """Координаты и метаданные от GPS."""
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    speed_kmh: Optional[float] = None
    timestamp: Optional[str] = None


def _nmea_to_decimal(nmea: str, direction: str) -> Optional[float]:
    """Конвертация NMEA (градусы+минуты) в десятичные градусы."""
    if not nmea or nmea == "":
        return None
    try:
        dot = nmea.index(".")
        deg = int(nmea[: dot - 2])  # градусы
        minutes = float(nmea[dot - 2 :])
        value = deg + minutes / 60.0
        if direction in ("S", "W"):
            value = -value
        return value
    except (ValueError, IndexError):
        return None


def _parse_gprmc(line: str) -> Optional[tuple]:
    """Парсит $GPRMC: возвращает (lat, lon) или None."""
    if not line.startswith("$GPRMC") and not line.startswith("$GNRMC"):
        return None
    parts = line.split(",")
    if len(parts) < 10:
        return None
    status = parts[2]
    if status != "A":  # A = valid
        return None
    lat = _nmea_to_decimal(parts[3], parts[4] if len(parts) > 4 else "N")
    lon = _nmea_to_decimal(parts[5], parts[6] if len(parts) > 6 else "E")
    if lat is not None and lon is not None:
        return (lat, lon)
    return None


def _parse_gpgga(line: str) -> Optional[tuple]:
    """Парсит $GPGGA: возвращает (lat, lon) или None."""
    if not line.startswith("$GPGGA") and not line.startswith("$GNGGA"):
        return None
    parts = line.split(",")
    if len(parts) < 9:
        return None
    fix = parts[6]
    if not fix or int(fix) == 0:
        return None
    lat = _nmea_to_decimal(parts[2], parts[3] if len(parts) > 3 else "N")
    lon = _nmea_to_decimal(parts[4], parts[5] if len(parts) > 5 else "E")
    if lat is not None and lon is not None:
        return (lat, lon)
    return None


def _parse_nmea_line(line: str) -> Optional[tuple]:
    """Парсит одну NMEA-строку, возвращает (lat, lon) или None."""
    line = line.strip()
    if not line:
        return None
    # Проверка контрольной суммы (опционально)
    if "*" in line:
        line = line.split("*")[0]
    result = _parse_gprmc(line) or _parse_gpgga(line)
    return result


class NetworkGps:
    """
    Сокет-клиент: подключается к телефону по TCP, читает NMEA,
    парсит GPRMC/GPGGA и отдаёт текущие координаты.
    При недоступности телефона возвращает последние известные или default из config.
    """

    def __init__(
        self,
        host: str = None,
        port: int = None,
        default_lat: float = None,
        default_lon: float = None,
        reconnect_interval: float = 5.0,
        recv_timeout: float = 2.0,
    ):
        self._host = host or PHONE_IP
        self._port = port or GPS_PORT
        self._default_lat = default_lat if default_lat is not None else DEFAULT_LAT
        self._default_lon = default_lon if default_lon is not None else DEFAULT_LON
        self._reconnect_interval = reconnect_interval
        self._recv_timeout = recv_timeout
        self._lock = threading.Lock()
        self._last_position = GpsPosition(
            latitude=self._default_lat,
            longitude=self._default_lon,
        )
        self._connected = False
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = False
        self._start_reader()

    def _start_reader(self) -> None:
        self._stop = False
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        buffer = b""
        while not self._stop:
            try:
                if self._sock is None:
                    self._connect()
                    if self._sock is None:
                        time.sleep(self._reconnect_interval)
                        continue
                self._sock.settimeout(self._recv_timeout)
                data = self._sock.recv(4096)
                if not data:
                    self._disconnect()
                    continue
                buffer += data
                while b"\r" in buffer or b"\n" in buffer:
                    line, sep, rest = buffer.partition(b"\n")
                    if not sep:
                        line, sep, rest = buffer.partition(b"\r")
                    buffer = rest
                    try:
                        text = line.decode("ascii", errors="ignore")
                    except Exception:
                        continue
                    coords = _parse_nmea_line(text)
                    if coords:
                        with self._lock:
                            self._last_position = GpsPosition(
                                latitude=coords[0],
                                longitude=coords[1],
                            )
                            self._connected = True
            except socket.timeout:
                continue
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                logger.debug("GPS connection error: %s", e)
                self._disconnect()
            except Exception as e:
                logger.debug("GPS read error: %s", e)
                self._disconnect()
            if self._sock is None:
                time.sleep(self._reconnect_interval)
        self._disconnect()

    def _connect(self) -> None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5.0)
            s.connect((self._host, self._port))
            self._sock = s
            self._connected = True
            logger.info("GPS: Connected (%s:%s)", self._host, self._port)
        except Exception as e:
            logger.debug("GPS connect failed %s:%s: %s", self._host, self._port, e)
            self._sock = None
            self._connected = False

    def _disconnect(self) -> None:
        was_connected = self._connected
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self._connected = False
        if was_connected:
            logger.info("GPS: Disconnected")

    def get_position(self) -> GpsPosition:
        """
        Возвращает последние известные координаты.
        Если телефон ни разу не подключался — координаты по умолчанию из config.
        """
        with self._lock:
            return GpsPosition(
                latitude=self._last_position.latitude,
                longitude=self._last_position.longitude,
                altitude=self._last_position.altitude,
                speed_kmh=self._last_position.speed_kmh,
                timestamp=self._last_position.timestamp,
            )

    def is_connected(self) -> bool:
        """True, если в данный момент есть соединение с телефоном."""
        return self._connected

    def stop(self) -> None:
        """Остановить фоновый поток (для выхода из приложения)."""
        self._stop = True
        self._disconnect()
        if self._thread:
            self._thread.join(timeout=3.0)


_default_gps: Optional[NetworkGps] = None


def get_gps(
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> NetworkGps:
    """Возвращает единственный экземпляр GPS-клиента (из config, если host/port не заданы)."""
    global _default_gps
    if _default_gps is None:
        _default_gps = NetworkGps(host=host, port=port)
    return _default_gps
