# -*- coding: utf-8 -*-
"""
Проверка камер: какие индексы доступны (для подбора Iriun Webcam и др.).
Запуск: python -m orbit_jetson.check_camera
"""

import sys
import time
import cv2

IS_WINDOWS = sys.platform == "win32"
CAP_MSMF = getattr(cv2, "CAP_MSMF", -1)

def main():
    print("Проверка камер (Iriun и др.). Закройте другие программы, использующие камеру.\n")
    for index in range(4):
        if IS_WINDOWS:
            for backend, name in [(cv2.CAP_DSHOW, "DSHOW"), (CAP_MSMF, "MSMF")]:
                if backend < 0:
                    continue
                cap = cv2.VideoCapture(index, backend)
                if cap.isOpened():
                    time.sleep(0.3)
                    ret, frame = cap.read()
                    cap.release()
                    status = "OK, кадр получен" if ret and frame is not None else "открылась, кадр пустой"
                    print(f"  Индекс {index} ({name}): {status}")
                    break
            else:
                print(f"  Индекс {index}: нет устройства")
        else:
            cap = cv2.VideoCapture(index)
            if cap.isOpened():
                ret, frame = cap.read()
                cap.release()
                status = "OK, кадр получен" if ret and frame is not None else "открылась, кадр пустой"
                print(f"  Индекс {index}: {status}")
            else:
                print(f"  Индекс {index}: нет устройства")
    print("\nВ config.py укажите CAMERA_INDEX = индекс, где 'OK' (для Iriun часто 0, 1 или 2).")

if __name__ == "__main__":
    main()
