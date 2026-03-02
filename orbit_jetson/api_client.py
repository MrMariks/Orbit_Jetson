# -*- coding: utf-8 -*-
"""
Клиент для отправки данных на Orbit_Backend.
"""

import base64
import logging
import time
from typing import Any, Dict, Optional, Union

import requests

from .config import API_TOKEN, BACKEND_URL
from .gps import GpsPosition

logger = logging.getLogger(__name__)


class OrbitApiClient:
    """Клиент API Orbit_Backend."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_token: Optional[str] = None,
        timeout: int = 10,
    ):
        self.base_url = (base_url or BACKEND_URL).rstrip("/")
        self.api_token = api_token or API_TOKEN
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        })
        self._logged_errors: set = set()  # (method, path, status_code) — не дублировать в консоль
        self._last_telemetry_ok = False
        self._last_detection_ok = False
        self._server_reachable = False  # True после успешной check_server() или любого запроса

    def check_server(self) -> bool:
        """
        Проверка доступности бэкенда. При старте мониторинга в статус-баре сразу «Сервер ✓»,
        не нужно ждать первой детекции.
        """
        for path in ("/", "/docs", "/api/v1"):
            url = self.base_url + path
            try:
                r = self._session.get(url, timeout=min(5, self.timeout))
                # Любой ответ = сервер доступен (200, 404, 405 — главное что отвечает)
                self._server_reachable = True
                logger.debug("Сервер %s", self.base_url)
                return True
            except requests.RequestException:
                continue
        logger.debug("Сервер недоступен: %s", self.base_url)
        return False

    def _request(self, method: str, path: str, json: Optional[Dict[str, Any]] = None) -> bool:
        """Выполняет запрос. Возвращает True при успехе."""
        url = f"{self.base_url}{path}"
        try:
            r = self._session.request(method, url, json=json, timeout=self.timeout)
            if r.ok:
                self._server_reachable = True
                if method == "POST" and path == "/api/v1/patrol/telemetry":
                    self._last_telemetry_ok = True
                return True
            key = (method, path, r.status_code)
            if key not in self._logged_errors:
                self._logged_errors.add(key)
                logger.warning("API %s %s: %s %s", method, path, r.status_code, r.text[:200])
            return False
        except requests.RequestException as e:
            logger.exception("API request failed: %s", e)
            return False

    def send_patrol_active(self) -> bool:
        """Патруль запустился: POST /api/v1/patrol/active (Authorization: Bearer)."""
        ok = self._request("POST", "/api/v1/patrol/active", json={})
        if ok:
            logger.info("Сервер: патруль отмечен как активный")
        return ok

    def send_patrol_finish(self) -> bool:
        """Патруль завершил работу: POST /api/v1/patrol/finish (Authorization: Bearer)."""
        ok = self._request("POST", "/api/v1/patrol/finish", json={})
        if ok:
            logger.info("Сервер: патруль отмечен как неактивный (завершение)")
        return ok

    def send_patrol_position(self, latitude: float, longitude: float) -> bool:
        """
        Отправляет координаты патруля на POST /api/v1/patrol/position для отображения иконки на карте.
        Заголовок: Authorization: Bearer <API_KEY>, тело: {"latitude": float, "longitude": float}.
        """
        payload = {"latitude": float(latitude), "longitude": float(longitude)}
        ok = self._request("POST", "/api/v1/patrol/position", json=payload)
        if ok:
            logger.debug("Сервер: координаты патруля отправлены (%.5f, %.5f)", latitude, longitude)
        return ok

    def send_telemetry(self, position: GpsPosition) -> bool:
        """
        Отправляет телеметрию на POST /api/v1/patrol/telemetry.
        Патруль определяется по API-токену (Authorization: Bearer).
        """
        payload = {
            "timestamp": position.timestamp or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        ok = self._request("POST", "/api/v1/patrol/telemetry", json=payload)
        if ok:
            logger.info("Сервер: телеметрия отправлена")
        return ok

    def send_detection(
        self,
        plate_text: str,
        full_image: Union[bytes, bytearray],
        crop_image: Union[bytes, bytearray],
    ) -> bool:
        """
        Отправляет детекцию на POST /api/v1/detect (JSON: plate_text + base64 изображения).
        """
        url = f"{self.base_url}/api/v1/detect"
        payload = {
            "plate_text": plate_text.strip(),
            "full_image": base64.b64encode(bytes(full_image)).decode("ascii"),
            "crop_image": base64.b64encode(bytes(crop_image)).decode("ascii"),
        }
        full_len = len(payload["full_image"])
        crop_len = len(payload["crop_image"])
        logger.info(
            "[Сервер] Отправка детекции: номер=%s, URL=%s, full_image=%s Кб, crop_image=%s Кб",
            plate_text.strip(), url, full_len // 1024, crop_len // 1024,
        )
        try:
            r = self._session.post(url, json=payload, timeout=self.timeout)
            if r.ok:
                self._server_reachable = True
                self._last_detection_ok = True
                logger.info("[Сервер] Детекция отправлена успешно (HTTP %s): %s", r.status_code, plate_text.strip())
                return True
            logger.warning(
                "[Сервер] Ошибка отправки детекции: HTTP %s, ответ: %s",
                r.status_code, r.text,
            )
            return False
        except requests.RequestException as e:
            logger.exception("[Сервер] Исключение при отправке детекции: %s", e)
            return False
