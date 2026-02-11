import requests
import json


def get_pavlodar_stable_points():
    # Используем другое зеркало, оно часто быстрее
    url = "https://overpass.kumi.systems/api/interpreter"

    # Ограничиваем поиск малым квадратом вокруг Квазара (bbox: юг, запад, север, восток)
    # Это исключит 504 ошибку
    query = """
    [out:json][timeout:30];
    (
      node["amenity"="parking"](52.26, 76.93, 52.32, 77.02);
      way["amenity"="parking"](52.26, 76.93, 52.32, 77.02);
    );
    out center;
    """

    print("Запрашиваю данные для центра Павлодара...")

    try:
        response = requests.get(url, params={'data': query}, timeout=40)
        if response.status_code == 200:
            data = response.json()
            elements = data.get('elements', [])

            parking_list = []
            for el in elements:
                lat = el.get('lat') or el.get('center', {}).get('lat')
                lon = el.get('lon') or el.get('center', {}).get('lon')

                if lat and lon:
                    parking_list.append({
                        "city": "Pavlodar",
                        "lat": round(lat, 6),
                        "lon": round(lon, 6),
                        "fee": "yes",
                        "price": 100
                    })

            with open('pavlodar_points.json', 'w', encoding='utf-8') as f:
                json.dump(parking_list, f, ensure_ascii=False, indent=4)

            print(f"Успех! Собрано {len(parking_list)} реальных точек.")
        else:
            print(f"Сервер всё еще занят (Код {response.status_code}). Попробуй через 10 секунд.")

    except Exception as e:
        print(f"Ошибка: {e}")


if __name__ == "__main__":
    get_pavlodar_stable_points()