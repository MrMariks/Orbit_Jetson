import json
from shapely.geometry import shape, Point
import os

# 1. Настраиваем правильный путь к файлу (учитывая папку data)
base_dir = os.path.dirname(os.path.abspath(__file__))
file_path = os.path.join(base_dir, 'data', 'map.geojson')


def check_parking_zone(geojson_file, current_lat, current_lon):
    """
    Проверяет, входит ли точка (машина) в какую-либо зону из GeoJSON.
    """
    try:
        # Загружаем размеченную карту
        with open(geojson_file, 'r', encoding='utf-8') as f:
            gj = json.load(f)
    except FileNotFoundError:
        print(f"Ошибка: Файл не найден по пути: {geojson_file}")
        return False, None

    # Создаем точку из текущих координат GPS
    # Важно: в GeoJSON формат обычно [Долгота, Широта] (Long, Lat)
    car_location = Point(current_lon, current_lat)

    # Перебираем все зоны в файле
    for feature in gj['features']:
        polygon = shape(feature['geometry'])
        zone_name = feature['properties'].get('name', 'Без названия')

        # Проверяем вхождение точки в полигон
        if polygon.contains(car_location):
            return True, zone_name

    return False, None


# Координаты для проверки
my_lat = 52.28543  # Широта
my_lon = 76.94125  # Долгота

# Запуск проверки
is_inside, name = check_parking_zone(file_path, my_lat, my_lon)

if is_inside:
    # Исправлено: используем переменную 'name', а не 'Miras_parking'
    print(f"✅ СИСТЕМА АКТИВИРОВАНА: Вы в зоне '{name}'.")
    print("Начинаю поиск и распознавание номеров...")
else:
    print("❌ Вне зоны: Сканирование отключено.")