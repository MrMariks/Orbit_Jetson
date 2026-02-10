import json
import os
import serial
import pynmea2
from shapely.geometry import shape, Point

# --- КОНФИГУРАЦИЯ ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_PATH = os.path.join(BASE_DIR, 'data', 'map.geojson')

# ДЛЯ WINDOWS: проверь в Диспетчере устройств (например, 'COM3')
# ДЛЯ LINUX/JETSON: обычно '/dev/ttyUSB0'
SERIAL_PORT = 'COM3'
BAUD_RATE = 9600


def load_zones(path):
    if not os.path.exists(path):
        print(f"❌ ОШИБКА: Файл карты отсутствует по пути {path}")
        return []
    with open(path, 'r', encoding='utf-8') as f:
        gj = json.load(f)
    return [{'poly': shape(f['geometry']), 'name': f['properties'].get('name', 'Zone')} for f in gj['features']]


def start_monitoring():
    zones = load_zones(FILE_PATH)
    if not zones: return

    try:
        # Подключаемся к GPS
        gps_serial = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.5)
        print(f"📡 Мониторинг запущен на {SERIAL_PORT}. Ожидание сигнала...")
    except Exception as e:
        print(f"❌ Ошибка порта: {e}. Проверь номер COM-порта!")
        return

    while True:
        try:
            line = gps_serial.readline().decode('ascii', errors='replace')

            # Парсим протокол NMEA (строка RMC содержит координаты и статус)
            if line.startswith('$GPRMC'):
                msg = pynmea2.parse(line)

                if msg.status == 'A':  # Сигнал активен
                    lat, lon = msg.latitude, msg.longitude
                    car_point = Point(lon, lat)

                    in_zone = False
                    for zone in zones:
                        if zone['poly'].contains(car_point):
                            print(f"✅ [В ЗОНЕ: {zone['name']}] Координаты: {lat:.5f}, {lon:.5f}")
                            in_zone = True
                            # Сюда в будущем добавим вызов YOLO
                            break

                    if not in_zone:
                        print(f"🚗 [ВНЕ ЗОНЫ] Координаты: {lat:.5f}, {lon:.5f}")
                else:
                    print("📡 Сигнал GPS слабый, ищу спутники...")

        except KeyboardInterrupt:
            print("\n🛑 Мониторинг остановлен пользователем.")
            break
        except Exception as e:
            print(f"⚠️ Ошибка данных: {e}")
            continue


if __name__ == "__main__":
    start_monitoring()