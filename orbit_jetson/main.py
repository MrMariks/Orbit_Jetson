# -*- coding: utf-8 -*-
"""
Точка входа приложения Orbit_Jetson.
Запуск: python -m orbit_jetson.main или из корня проекта: python orbit_jetson/main.py
"""

import logging
import sys
import threading
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
# Если ALPR_USE_CPU — скрываем GPU от PyTorch до импорта, чтобы nomeroff-net считал на CPU
import os
from . import config as _config
if getattr(_config, "ALPR_USE_CPU", False):
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
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

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

from .config import CAMERA_INDEX
from .api_client import OrbitApiClient
from .gps import get_gps
from .camera import CameraWorker, preload_alpr, warmup_camera
from .ui import MainWindow


def main():
    # Сразу выводить логи в консоль (важно для ноутбука / PowerShell)
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(line_buffering=True)
            sys.stdout.reconfigure(line_buffering=True)
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("orbit_jetson")
    logger.info("Старт программы")

    api = OrbitApiClient()
    gps = get_gps()
    camera = CameraWorker(camera_index=CAMERA_INDEX)

    app = QApplication(sys.argv)
    app.setApplicationName("Orbit_Jetson")

    win = MainWindow(camera_worker=camera, api_client=api, gps_stub=gps)
    win.showMaximized()
    app.processEvents()

    loading_done = [False]

    def load_task():
        preload_alpr()
        api.check_server()
        warmup_camera()  # прогрев камеры — при первом «Старт мониторинга» не зависает
        loading_done[0] = True

    def on_loading_check():
        if loading_done[0]:
            win.show_main_content()
            # Один лог по 5 модулям: да/нет
            server_ok = getattr(api, "_server_reachable", False)
            gps_ok = getattr(gps, "is_connected", lambda: False)()
            logger.info(
                "ПО да | Модель да | Сервер %s | Камера нет | GPS %s",
                "да" if server_ok else "нет",
                "да" if gps_ok else "нет",
            )
            return
        QTimer.singleShot(350, on_loading_check)

    threading.Thread(target=load_task, daemon=True).start()
    QTimer.singleShot(350, on_loading_check)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
