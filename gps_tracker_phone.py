import socket
import pynmea2
import json
import os
from shapely.geometry import shape, Point

# --- НАСТРОЙКИ (Возьми из приложения на телефоне) ---
IP_ADDRESS = '192.168.1.1'
PORT = 8080

# --- НАСТРОЙКИ КАРТЫ ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_PATH = os.path.join(BASE_DIR, 'data', 'map.geojson')


def load_zones():
    if not os.path.exists(FILE_PATH):
        print(f"❌ Ошибка: Файл {FILE_PATH} не найден!")
        return []
    with open(FILE_PATH, 'r', encoding='utf-8') as f:
        gj = json.load(f)
    return [{'poly': shape(f['geometry']), 'name': f['properties'].get('name', 'Zone')} for f in gj['features']]


def start_tracking():
    zones = load_zones()
    if not zones: return

    # Создаем сокет для подключения к телефону
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)  # Ждем подключения 10 секунд

    try:
        print(f"📡 Подключаюсь к телефону {IP_ADDRESS}:{PORT}...")
        sock.connect((IP_ADDRESS, PORT))
        print("✅ Соединение установлено! Начни движение или вынеси телефон к окну.")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        print("Подсказка: Проверь, что телефон и компьютер в ОДНОЙ сети Wi-Fi.")
        return

    while True:
        try:
            data = sock.recv(1024).decode('ascii', errors='replace')
            if not data: continue

            # Разрезаем данные на строки NMEA
            lines = data.split('\r\n')
            for line in lines:
                # Нас интересуют строки RMC или GGA (в них есть координаты)
                if '$GPRMC' in line or '$GNRMC' in line or '$GPGGA' in line:
                    try:
                        msg = pynmea2.parse(line)
                        # Проверяем, что сигнал валидный
                        if hasattr(msg, 'status') and msg.status == 'V':
                            print("📡 Сигнал GPS еще не пойман (статус V)...")
                            continue

                        lat, lon = msg.latitude, msg.longitude
                        car_point = Point(lon, lat)

                        in_zone = False
                        for zone in zones:
                            if zone['poly'].contains(car_point):
                                print(f"📍 [В ЗОНЕ: {zone['name']}] Координаты: {lat:.5f}, {lon:.5f}")
                                in_zone = True
                                break

                        if not in_zone:
                            print(f"🚗 [ВНЕ ЗОНЫ] Координаты: {lat:.5f}, {lon:.5f}")

                    except Exception:
                        continue
        except KeyboardInterrupt:
            print("\n🛑 Тест остановлен.")
            break
        except Exception as e:
            print(f"⚠️ Ошибка связи: {e}")
            break
    sock.close()


if __name__ == "__main__":
    start_tracking()