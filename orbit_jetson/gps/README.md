# Пакет GPS

Сюда вынесена вся логика приёма геолокации:

- **client.py** — TCP-клиент NMEA (подключение к телефону), парсинг GPRMC/GPGGA, приём по HTTP (браузер на телефоне), агрегатор источников.
- Настройки (IP телефона, порты, координаты по умолчанию) остаются в **orbit_jetson/config.py** (секция «GPS по TCP» и `GPS_HTTP_PORT`).

## Использование в проекте

```python
from orbit_jetson.gps import get_gps, GpsPosition

gps = get_gps()
pos = gps.get_position()  # GpsPosition(latitude=..., longitude=...)
```

## Если нужно удалить GPS

1. Удалите папку **orbit_jetson/gps/** целиком.
2. В **orbit_jetson/main.py** уберите импорт `get_gps` и создание окна с `gps_stub` — передавайте заглушку с методами `get_position()` (возвращает объект с `latitude`, `longitude` из config) и `is_connected()` (возвращает `False`).
3. В **orbit_jetson/api_client.py** уберите импорт `GpsPosition` из gps; телеметрию можно отправлять с фиксированными координатами из config или отключить.
4. В **orbit_jetson/ui.py** карта и статус «GPS» будут работать от заглушки (статичная точка на карте).
