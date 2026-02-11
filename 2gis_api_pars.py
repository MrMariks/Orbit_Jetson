import requests
import json


def get_2gis_parkings_final(api_key):
    # Используем базовый эндпоинт поиска
    url = "https://catalog.api.2gis.com/3.0/items"

    params = {
        "key": api_key,
        "q": "парковка",
        # Важно: 2ГИС ждет lon,lat (долгота первая!)
        "point": "76.967447,52.298478",
        "radius": "10000",  # 10 км
        "fields": "items.point",
        "page_size": 50
    }

    print(f"Отправляю запрос к 2ГИС (центр: {params['point']})...")

    try:
        response = requests.get(url, params=params, timeout=10)

        if response.status_code == 200:
            data = response.json()
            # 2ГИС может вернуть ошибку внутри JSON (например, 'результатов не найдено')
            result = data.get('result', {})
            items = result.get('items', [])

            if not items:
                print("2ГИС ответил 200 OK, но список объектов пуст. Попробуем сменить 'q' на 'автостоянка'...")
                params["q"] = "автостоянка"
                response = requests.get(url, params=params)
                items = response.json().get('result', {}).get('items', [])

            parking_points = []
            for item in items:
                point = item.get('point')
                if point:
                    parking_points.append({
                        "city": "Pavlodar",
                        "name": item.get('name', 'Парковка'),
                        "lat": point['lat'],
                        "lon": point['lon'],
                        "fee": "yes",
                        "price": 100
                    })

            with open('pavlodar_2gis_points.json', 'w', encoding='utf-8') as f:
                json.dump(parking_points, f, ensure_ascii=False, indent=4)

            print(f"Успех! Найдено {len(parking_points)} точек.")
        else:
            print(f"Ошибка API: {response.status_code}")
            print(f"Текст ошибки: {response.text}")

    except Exception as e:
        print(f"Критическая ошибка: {e}")


# Вставь свой ключ (он должен быть длинным набором символов)
MY_2GIS_KEY = 'e728e1ad-bbb7-415b-b572-4d0a7649f37c'
get_2gis_parkings_final(MY_2GIS_KEY)