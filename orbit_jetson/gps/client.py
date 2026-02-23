# -*- coding: utf-8 -*-
"""
Клиент GPS: TCP NMEA и опционально приём по HTTP.
"""

import logging
import re
import socket
import ssl
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

try:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlparse
except ImportError:
    BaseHTTPRequestHandler = None  # type: ignore
    HTTPServer = None  # type: ignore
    parse_qs = urlparse = None

from ..config import (
    DEFAULT_LAT,
    DEFAULT_LON,
    GPS_HTTP_PORT,
    GPS_HTTP_USE_HTTPS,
    GPS_HTTP_CERT_DIR,
    GPS_PORT,
    PHONE_IP,
)

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
    if "*" in line:
        line = line.split("*")[0]
    return _parse_gprmc(line) or _parse_gpgga(line)


class NetworkGps:
    """
    Сокет-клиент: подключается к телефону по TCP, читает NMEA,
    парсит GPRMC/GPGGA и отдаёт текущие координаты.
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
        with self._lock:
            return GpsPosition(
                latitude=self._last_position.latitude,
                longitude=self._last_position.longitude,
                altitude=self._last_position.altitude,
                speed_kmh=self._last_position.speed_kmh,
                timestamp=self._last_position.timestamp,
            )

    def is_connected(self) -> bool:
        return self._connected

    def stop(self) -> None:
        self._stop = True
        self._disconnect()
        if self._thread:
            self._thread.join(timeout=3.0)


def _create_cert_with_cryptography(cert_file: Path, key_file: Path) -> bool:
    """Создаёт cert.pem и key.pem через библиотеку cryptography (без openssl)."""
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
        from datetime import datetime, timedelta
    except ImportError:
        return False
    try:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.utcnow())
            .not_valid_after(datetime.utcnow() + timedelta(days=365))
            .sign(key, hashes.SHA256())
        )
        key_file.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()))
        cert_file.write_bytes(cert.public_bytes(Encoding.PEM))
        return True
    except Exception:
        return False


def _get_or_create_cert_dir() -> Optional[Path]:
    """Возвращает каталог с cert.pem и key.pem; при необходимости создаёт их (openssl или cryptography)."""
    cert_dir = (GPS_HTTP_CERT_DIR or "").strip()
    if cert_dir:
        path = Path(cert_dir)
    else:
        path = Path(__file__).resolve().parent / "certs"
    path.mkdir(parents=True, exist_ok=True)
    cert_file = path / "cert.pem"
    key_file = path / "key.pem"
    if cert_file.is_file() and key_file.is_file():
        return path
    # Сначала пробуем openssl
    try:
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(key_file), "-out", str(cert_file),
                "-days", "365", "-nodes", "-subj", "/CN=localhost",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        logger.info("GPS HTTPS: созданы самоподписанные сертификаты в %s (openssl)", path)
        return path
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Fallback: создаём через Python (cryptography)
    if _create_cert_with_cryptography(cert_file, key_file):
        logger.info("GPS HTTPS: созданы самоподписанные сертификаты в %s (cryptography)", path)
        return path
    logger.warning(
        "GPS HTTPS: не удалось создать сертификаты (нужен openssl в PATH или pip install cryptography). Запуск по HTTP."
    )
    return None


def _run_http_gps_server(port: int, on_position: Callable[[float, float], None]) -> None:
    """HTTP(S)-сервер: GET /?lat=...&lon=... или GET / для страницы с запросом геолокации."""
    if HTTPServer is None or BaseHTTPRequestHandler is None or parse_qs is None or urlparse is None:
        return

    use_https = bool(GPS_HTTP_USE_HTTPS)
    cert_dir: Optional[Path] = None
    if use_https:
        cert_dir = _get_or_create_cert_dir()
        if cert_dir is None:
            use_https = False

    HTML_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>GPS</title></head><body><p>Отправка геолокации на ПК…</p><script>
(function(){
  if (!navigator.geolocation) { document.body.innerHTML="<p>Геолокация недоступна.</p>"; return; }
  navigator.geolocation.getCurrentPosition(function(pos){
    var lat=pos.coords.latitude, lon=pos.coords.longitude;
    window.location.href="/?lat="+lat+"&lon="+lon;
  }, function(){ document.body.innerHTML="<p>Доступ к геолокации запрещён.</p>"; });
})();
</script></body></html>"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path.rstrip("/") or "/"
            has_coords = False
            try:
                qs = parse_qs(urlparse(self.path).query)
                lat_s = (qs.get("lat") or [None])[0]
                lon_s = (qs.get("lon") or [None])[0]
                if lat_s is not None and lon_s is not None:
                    lat_f = float(lat_s)
                    lon_f = float(lon_s)
                    if -90 <= lat_f <= 90 and -180 <= lon_f <= 180:
                        on_position(lat_f, lon_f)
                        logger.info("GPS HTTP: получены координаты %.5f, %.5f", lat_f, lon_f)
                        has_coords = True
            except (ValueError, TypeError, IndexError):
                pass
            self.send_response(200)
            if path == "/" and not has_coords:
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(HTML_PAGE.encode("utf-8"))
            else:
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"OK")

        def log_message(self, format, *args):
            logger.debug("GPS HTTP: %s", args[0] if args else "")

    try:
        server = HTTPServer(("0.0.0.0", port), Handler)
        if use_https and cert_dir is not None:
            cert_file = cert_dir / "cert.pem"
            key_file = cert_dir / "key.pem"
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(cert_file), str(key_file))
            server.socket = ctx.wrap_socket(server.socket, server_side=True)
            scheme = "https"
        else:
            scheme = "http"
        logger.info(
            "GPS HTTP: приём координат на порту %s (%s). Откройте на телефоне: %s://IP_ПК:%s/",
            port, scheme.upper(), scheme, port,
        )
        server.serve_forever()
    except Exception as e:
        logger.warning("GPS HTTP сервер: %s", e)


class GpsAggregator:
    """
    Объединяет NMEA (TCP) и опционально HTTP: приоритет у NMEA при подключённом телефоне,
    иначе — последние координаты по HTTP (если не старше 2 минут).
    """

    def __init__(self, network_gps: NetworkGps, http_port: int = 0):
        self._nmea = network_gps
        self._http_port = max(0, int(http_port))
        self._http_lat: Optional[float] = None
        self._http_lon: Optional[float] = None
        self._http_time: float = 0.0
        self._http_lock = threading.Lock()
        if self._http_port > 0 and HTTPServer is not None:
            threading.Thread(
                target=_run_http_gps_server,
                args=(self._http_port, self._set_http_position),
                daemon=True,
            ).start()

    def _set_http_position(self, lat: float, lon: float) -> None:
        with self._http_lock:
            self._http_lat = lat
            self._http_lon = lon
            self._http_time = time.time()

    def get_position(self) -> GpsPosition:
        if self._nmea.is_connected():
            return self._nmea.get_position()
        with self._http_lock:
            if self._http_lat is not None and self._http_lon is not None and (time.time() - self._http_time) < 120.0:
                return GpsPosition(latitude=self._http_lat, longitude=self._http_lon)
        return self._nmea.get_position()

    def is_connected(self) -> bool:
        if self._nmea.is_connected():
            return True
        with self._http_lock:
            return (self._http_lat is not None and (time.time() - self._http_time) < 120.0)

    def stop(self) -> None:
        self._nmea.stop()


_default_gps: Optional[NetworkGps] = None
_default_aggregator: Optional["GpsAggregator"] = None


def get_gps(
    host: Optional[str] = None,
    port: Optional[int] = None,
):
    """Возвращает единственный экземпляр GPS (NMEA TCP; при GPS_HTTP_PORT > 0 — с приёмом по HTTP)."""
    global _default_gps, _default_aggregator
    if _default_gps is None:
        _default_gps = NetworkGps(host=host, port=port)
    if GPS_HTTP_PORT and _default_aggregator is None:
        _default_aggregator = GpsAggregator(_default_gps, GPS_HTTP_PORT)
        return _default_aggregator
    if _default_aggregator is not None:
        return _default_aggregator
    return _default_gps
