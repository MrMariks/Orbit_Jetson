import json
from shapely.geometry import shape, Point\
import os

# Получаем путь к папке, где лежит сам скрипт main.py
base_dir = os.path.dirname(os.path.abspath(__file__))

# Соединяем путь папки с названием файла
file_path = os.path.join(base_dir, 'map.geojson')

with open(file_path, 'r', encoding='utf-8') as f:

def check_parking_zone(geojson_file, current_lat, current_lon):
    """
    Проверяет, входит ли точка (машина) в какую-либо зону из GeoJSON.
    """
    # 1. Загружаем размеченную карту
    with open(geojson_file, 'r', encoding='utf-8') as f:
        gj = json.load(f)

    # 2. Создаем точку из текущих координат GPS
    # Важно: в GeoJSON формат обычно [Долгота, Широта] (Long, Lat)
    car_location = Point(current_lon, current_lat)

    # 3. Перебираем все зоны в файле (если их несколько)123321
    for feature in gj['features']:
        polygon = shape(feature['geometry'])
        zone_name = feature['properties'].get('name', 'Без названия')
        
        # Проверяем вхождение точки в полигон
        if polygon.contains(car_location):
            return True, zone_name
            
    return False, None

# --- ТЕСТОВЫЙ БЛОК ---

# Путь к твоему файлу, который ты скачал с geojson.iojkhdfkgjkdfgjjkdfgj
file_path = 'data/map.geojson'

# Пример координат (подставь свои реальные данные из GPS)
# Допустим, ты стоишь у ТЦ Квазар в Павлодаре
my_lat = 52.28543  # Широта
my_lon = 76.94125  # Долгота

is_inside, name = check_parking_zone(file_path, my_lat, my_lon)

if is_inside:
    print(f"✅ СИСТЕМА АКТИВИРОВАНА: Вы в зоне '{Miras_parking}'.")
    print("Начинаю поиск и распознавание номеров...")
else:
    print("❌ Вне зоны: Сканирование отключено.")