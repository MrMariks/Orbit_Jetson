# -*- coding: utf-8 -*-
"""
Точка входа приложения Orbit_Jetson.
Запуск: python -m orbit_jetson.main или из корня проекта: python orbit_jetson/main.py
"""

import logging
import sys
import warnings

# pkg_resources нужен для nomeroff-net; в setuptools 82.0+ его убрали — нужен setuptools<82
with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=UserWarning)
    try:
        import pkg_resources  # noqa: F401
    except ModuleNotFoundError:
        print(
            "Внимание: модуль pkg_resources не найден (в setuptools 82+ его убрали).\n"
            "Распознавание номеров будет отключено. Чтобы включить, выполните в терминале:\n"
            "  pip install \"setuptools>=65,<82\"\n"
            "и перезапустите приложение."
        )

# Важно: загрузить PyTorch до PyQt5, иначе на Windows возможна ошибка загрузки DLL (WinError 1114 / c10.dll)
try:
    import torch  # noqa: F401
except Exception:
    pass

# Предупреждения при загрузке nomeroff-net и torch (не влияют на работу)
warnings.filterwarnings("ignore", category=UserWarning, module="nomeroff_net")
warnings.filterwarnings("ignore", category=UserWarning, module="torchvision")
warnings.filterwarnings("ignore", category=FutureWarning, module="torch")
warnings.filterwarnings("ignore", category=FutureWarning, module="lightning_lite")
warnings.filterwarnings("ignore", category=FutureWarning, message=".*torch\\.load.*weights_only.*")
warnings.filterwarnings("ignore", message=".*Creating a tensor from a list.*")

from PyQt5.QtWidgets import QApplication

from .config import CAMERA_INDEX, PHONE_IP, GPS_PORT
from .api_client import OrbitApiClient
from .gps import get_gps
from .camera import CameraWorker
from .ui import MainWindow


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("orbit_jetson")
    logger.info("Orbit_Jetson starting")
    logger.info("Камера: индекс %s (статус при нажатии «Старт мониторинга»)", CAMERA_INDEX)
    logger.info("GPS: подключение к %s:%s (статус в консоли при подключении/обрыве)", PHONE_IP, GPS_PORT)
    logger.info("Распознавание номеров: ALPR (nomeroff-net), загрузка при старте мониторинга")

    api = OrbitApiClient()
    gps = get_gps()
    camera = CameraWorker(camera_index=CAMERA_INDEX)

    app = QApplication(sys.argv)
    app.setApplicationName("Orbit_Jetson")
    win = MainWindow(camera_worker=camera, api_client=api, gps_stub=gps)
    win.showMaximized()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
