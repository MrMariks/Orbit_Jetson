@echo off
chcp 65001 >nul
echo Скачивание всех зависимостей в папку для переноса на другой ПК...
if not exist "offline_packages" mkdir offline_packages

REM Скачать пакеты по списку (без git-зависимости — её на другом ПК ставят отдельно при необходимости)
pip download -r requirements.txt -d offline_packages
if errorlevel 1 (
    echo Предупреждение: часть пакетов могла не скачаться. Проверьте вывод выше.
) else (
    echo Готово. Папка offline_packages создана.
)

REM Зафиксировать текущие версии (для установки на другом ПК в том же составе)
pip freeze > requirements_frozen.txt
echo Текущие версии записаны в requirements_frozen.txt

echo.
echo На другой ПК: скопируйте всю папку проекта включая offline_packages.
echo Затем см. файл INSTALL_OFFLINE.txt
pause
