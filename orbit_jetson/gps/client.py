# -*- coding: utf-8 -*-
"""
Клиент GPS: приём координат только через браузер (HTTP).
На телефоне открываете страницу, даёте доступ к геолокации — координаты уходят на ПК.
"""

import logging
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

from .config import (
    DEFAULT_LAT,
    DEFAULT_LON,
    GPS_HTTP_PORT,
)

GPS_HTTP_USE_HTTPS = False
GPS_HTTP_CERT_DIR = ""

logger = logging.getLogger(__name__)


@dataclass
class GpsPosition:
    """Координаты от GPS (браузер)."""
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    speed_kmh: Optional[float] = None
    timestamp: Optional[str] = None


def _get_local_ip() -> Optional[str]:
    """Локальный IP в текущей сети (Wi‑Fi/раздача)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return None


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
    """Возвращает каталог с cert.pem и key.pem для HTTPS (если нужен)."""
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
        logger.info("GPS HTTPS: созданы сертификаты в %s", path)
        return path
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass
    if _create_cert_with_cryptography(cert_file, key_file):
        logger.info("GPS HTTPS: созданы сертификаты в %s (cryptography)", path)
        return path
    return None


def _run_http_gps_server(port: int, on_position: Callable[[float, float], None]) -> None:
    """HTTP(S)-сервер: GET /?lat=...&lon=... или GET / — страница с запросом геолокации."""
    if HTTPServer is None or BaseHTTPRequestHandler is None or parse_qs is None or urlparse is None:
        return

    use_https = bool(GPS_HTTP_USE_HTTPS)
    cert_dir: Optional[Path] = None
    if use_https:
        cert_dir = _get_or_create_cert_dir()
        if cert_dir is None:
            use_https = False

    HTML_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>GPS — Orbit</title></head><body><p id="msg">Запрос доступа к геолокации…</p><p id="coords"></p><script>
(function(){
  if (!navigator.geolocation) { document.getElementById("msg").textContent = "Геолокация недоступна."; return; }
  function send(lat, lon) {
    var x = new XMLHttpRequest();
    x.open("GET", "/?lat=" + lat + "&lon=" + lon, true);
    x.send();
  }
  function onPos(pos) {
    var lat = pos.coords.latitude, lon = pos.coords.longitude;
    document.getElementById("msg").textContent = "Координаты отправляются на ПК.";
    document.getElementById("coords").textContent = lat.toFixed(5) + ", " + lon.toFixed(5);
    send(lat, lon);
  }
  function onErr() { document.getElementById("msg").textContent = "Доступ к геолокации запрещён или ошибка."; }
  navigator.geolocation.getCurrentPosition(onPos, onErr, { enableHighAccuracy: true });
  navigator.geolocation.watchPosition(onPos, onErr, { enableHighAccuracy: true, maximumAge: 5000 });
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
                        logger.info("GPS: получены координаты %.5f, %.5f", lat_f, lon_f)
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
        local_ip = _get_local_ip() or "IP_ПК"
        url = "%s://%s:%s/" % (scheme, local_ip, port)
        logger.info("GPS: приём на порту %s (%s). На телефоне откройте: %s", port, scheme.upper(), url)
        server.serve_forever()
    except Exception as e:
        logger.warning("GPS HTTP сервер: %s", e)


class BrowserGps:
    """Приём координат только через браузер (HTTP)."""

    def __init__(self, http_port: int = 0, default_lat: float = None, default_lon: float = None):
        self._http_port = max(0, int(http_port))
        self._default_lat = default_lat if default_lat is not None else DEFAULT_LAT
        self._default_lon = default_lon if default_lon is not None else DEFAULT_LON
        self._lat: Optional[float] = None
        self._lon: Optional[float] = None
        self._last_time: float = 0.0
        self._lock = threading.Lock()
        if self._http_port > 0 and HTTPServer is not None:
            threading.Thread(
                target=_run_http_gps_server,
                args=(self._http_port, self._set_position),
                daemon=True,
            ).start()

    def _set_position(self, lat: float, lon: float) -> None:
        with self._lock:
            self._lat = lat
            self._lon = lon
            self._last_time = time.time()

    def get_position(self) -> GpsPosition:
        with self._lock:
            if self._lat is not None and self._lon is not None and (time.time() - self._last_time) < 120.0:
                return GpsPosition(latitude=self._lat, longitude=self._lon)
            return GpsPosition(latitude=self._default_lat, longitude=self._default_lon)

    def is_connected(self) -> bool:
        with self._lock:
            return self._lat is not None and (time.time() - self._last_time) < 120.0

    def stop(self) -> None:
        pass


_default_gps: Optional[BrowserGps] = None


def get_gps():
    """Возвращает единственный экземпляр GPS (только через браузер)."""
    global _default_gps
    if _default_gps is None:
        port = int(GPS_HTTP_PORT) if GPS_HTTP_PORT else 0
        _default_gps = BrowserGps(http_port=port)
    return _default_gps
