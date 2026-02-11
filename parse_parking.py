import requests
import json


def get_astana_total_paid():
    url = "https://overpass.kumi.systems/api/interpreter"

    # Запрос ищет:
    # 1. Любые объекты с тегом fee=yes
    # 2. Улицы, у которых размечена парковочная полоса (parking:lane)
    # 3. Объекты с оператором парковок
    query = """
    [out:json][timeout:180];
    area["name"="Астана"]->.searchArea;
    (
      node["amenity"="parking"](area.searchArea);
      way["amenity"="parking"](area.searchArea);
      way["parking:lane:both"](area.searchArea);
      way["parking:lane:right"](area.searchArea);
      way["parking:lane:left"](area.searchArea);
    );
    out center;
    """

    print("Запуск глубокого сканирования инфраструктуры Астаны...")

    try:
        response = requests.get(url, params={'data': query}, timeout=200)
        if response.status_code == 200:
            elements = response.json().get('elements', [])

            final_data = []
            for el in elements:
                tags = el.get('tags', {})

                # Фильтр: берем только то, что с большой вероятностью платно
                # В центре Астаны (Есильский, Алматинский р-ны) почти все уличные парковки платные
                is_paid = any([
                    tags.get('fee') == 'yes',
                    'parking:lane' in str(tags.keys()),
                    'Парков' in tags.get('operator', ''),
                    'Парков' in tags.get('name', '')
                ])

                if is_paid:
                    lat = el.get('lat') or el.get('center', {}).get('lat')
                    lon = el.get('lon') or el.get('center', {}).get('lon')

                    final_data.append({
                        "name": tags.get('name', 'Городская парковочная зона'),
                        "lat": lat,
                        "lon": lon,
                        "address": f"{tags.get('addr:street', '')} {tags.get('addr:housenumber', '')}".strip(),
                        "price": "100 KZT/hour",
                        "type": "street_side" if 'parking:lane' in str(tags) else "surface"
                    })

            # Удаляем дубликаты
            unique_data = {(round(p['lat'], 4), round(p['lon'], 4)): p for p in final_data}.values()

            filename = 'astana_all_paid_zones.json'
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(list(unique_data), f, ensure_ascii=False, indent=4)

            print(f"Победа! Собрано объектов: {len(unique_data)}")
        else:
            print(f"Ошибка: {response.status_code}")
    except Exception as e:
        print(f"Ошибка соединения: {e}")


if __name__ == "__main__":
    get_astana_total_paid()