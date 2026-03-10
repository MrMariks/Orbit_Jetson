# -*- coding: utf-8 -*-
"""
Точка входа приложения Orbit_Jetson.
Запуск: python -m orbit_jetson.main или из корня проекта: python orbit_jetson/main.py
"""

import logging
import subprocess
import sys
import threading
import warnings
from pathlib import Path

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
from .gps.config import GPS_HTTP_PORT, GPS_HTTP_USE_HTTPS
from .camera import CameraWorker, preload_alpr, warmup_camera
from .ui import MainWindow


def main():
    # Общий вывод логов отключён; только статусные строки в консоль
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.CRITICAL)
    root.addHandler(logging.NullHandler())

    # Лог статуса: только эти строки выводятся
    status = logging.getLogger("orbit_jetson.status")
    status.setLevel(logging.INFO)
    status.propagate = False
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(message)s"))
    status.addHandler(h)

    status.info("Старт программы")

    api = OrbitApiClient()
    gps = get_gps()
    camera = CameraWorker(camera_index=CAMERA_INDEX)

    # Запуск моста Firebase → Jetson (из папки gps) вместе с ПО
    bridge_process = None
    gps_dir = Path(__file__).resolve().parent / "gps"
    bridge_script = gps_dir / "bridge.py"
    if bridge_script.is_file():
        try:
            env = os.environ.copy()
            env["LOCAL_PORT"] = str(GPS_HTTP_PORT or 8765)
            env["LOCAL_HTTPS"] = "1" if GPS_HTTP_USE_HTTPS else "0"
            bridge_process = subprocess.Popen(
                [sys.executable, "-u", "bridge.py"],
                cwd=str(gps_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
            )
        except Exception:
            pass

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
            server_ok = getattr(api, "_server_reachable", False)
            gps_ok = getattr(gps, "is_connected", lambda: False)()
            # В лог — тот же формат, что и в статус-баре UI (ПО — | Модель — | Сервер ✓ | Камера — | GPS ✓)
            s_server = "Сервер ✓" if server_ok else "Сервер —"
            s_gps = "GPS ✓" if gps_ok else "GPS —"
            status.info("ПО — | Модель — | %s | Камера — | %s", s_server, s_gps)
            # Если GPS пока нет, но мост запущен — периодически проверять и вывести "GPS ✓" когда появятся данные
            if bridge_process is not None and not gps_ok:
                gps_ok_logged = [False]

                def check_gps_later():
                    if gps_ok_logged[0]:
                        return
                    if getattr(gps, "is_connected", lambda: False)():
                        status.info("GPS ✓")
                        gps_ok_logged[0] = True

                def schedule_check():
                    check_gps_later()
                    if not gps_ok_logged[0]:
                        QTimer.singleShot(6000, schedule_check)

                QTimer.singleShot(6000, schedule_check)
            return
        QTimer.singleShot(350, on_loading_check)

    threading.Thread(target=load_task, daemon=True).start()
    QTimer.singleShot(350, on_loading_check)

    if bridge_process is not None:
        def _stop_bridge():
            bridge_process.terminate()
            try:
                bridge_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                bridge_process.kill()
        app.aboutToQuit.connect(_stop_bridge)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
