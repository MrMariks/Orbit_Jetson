from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import json
import time


def scrape_astana_parking_live():
    chrome_options = Options()
    # Запускаем без окна браузера (headless), чтобы не мешало
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    # Маскируемся под человека
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

    print("Запускаю браузер и захожу на сайт Astanapark...")

    try:
        # Заходим на страницу карты
        driver.get("https://astanapark.kz/")
        time.sleep(10)  # Даем время на прогрузку скриптов и карты

        # Пытаемся вытащить данные напрямую из JS-контекста страницы
        # Часто данные о парковках лежат в глобальных переменных вроде 'parkingZones' или 'mapData'
        script = "return window.parkingZones || window.mapObjects || JSON.stringify(performance.getEntriesByType('resource'));"
        result = driver.execute_script(script)

        if result:
            with open('raw_capture.txt', 'w', encoding='utf-8') as f:
                f.write(str(result))
            print("Что-то поймали! Проверь файл raw_capture.txt")
        else:
            print("Данные в памяти страницы не найдены.")

    except Exception as e:
        print(f"Ошибка при работе браузера: {e}")
    finally:
        driver.quit()


if __name__ == "__main__":
    scrape_astana_parking_live()