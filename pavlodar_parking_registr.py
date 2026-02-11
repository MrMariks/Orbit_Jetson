import json
import random


def generate_pavlodar_parking_data():
    # Основные зоны Павлодара, где есть платные парковки и стоянки
    zones = [
        {"name": "Район Batyr Mall / Ак. Сатпаева", "lat": (52.260, 52.275), "lon": (76.980, 77.010)},
        {"name": "Центр (ул. Академика Чокина / пр. Назарбаева)", "lat": (52.280, 52.300), "lon": (76.930, 76.960)},
        {"name": "Район рынков (Квазар / Асыл)", "lat": (52.295, 52.310), "lon": (76.960, 76.985)},
        {"name": "Набережная / ул. Астана", "lat": (52.275, 52.300), "lon": (76.915, 76.935)},
        {"name": "Район ЖД Вокзала", "lat": (52.290, 52.305), "lon": (76.900, 76.925)},
        {"name": "Дачный микрорайон (платные стоянки)", "lat": (52.230, 52.250), "lon": (76.960, 76.990)},
        {"name": "Усольский микрорайон", "lat": (52.245, 52.265), "lon": (76.920, 76.950)}
    ]

    parking_data = []
    object_id = 7001  # Начнем ID для Павлодара с 7000

    for zone in zones:
        # Генерируем по 75-80 точек на каждую зону для достижения нужного объема
        num_points = random.randint(75, 85)
        for _ in range(num_points):
            lat = round(random.uniform(zone["lat"][0], zone["lat"][1]), 6)
            lon = round(random.uniform(zone["lon"][0], zone["lon"][1]), 6)

            # В Павлодаре цена чаще фиксированная за сутки или час
            price = random.choice([100, 150, 200])

            parking_data.append({
                "city": "Pavlodar",
                "lat": lat,
                "lon": lon,
                "fee": "yes",
                "price": price
            })
            object_id += 1

    with open('pavlodar_fixed_registry.json', 'w', encoding='utf-8') as f:
        json.dump(parking_data, f, ensure_ascii=False, indent=4)

    print(f"Готово! Сгенерировано {len(parking_data)} объектов для Павлодара.")
    print("Данные готовы для загрузки в Orbit_Jetson.")


if __name__ == "__main__":
    generate_pavlodar_parking_data()