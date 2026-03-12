# -*- coding: utf-8 -*-
# DEBUG ONLY — удалить при релизе.
# Логика загрузки фото для отладки: выбор файла и запуск ALPR по одному изображению.

from typing import List, Optional, Tuple

from PyQt5.QtWidgets import QFileDialog, QWidget


def get_photo_path(parent: Optional[QWidget] = None) -> Optional[str]:
    """Открыть диалог выбора изображения. Возвращает путь или None."""
    path, _ = QFileDialog.getOpenFileName(
        parent,
        "Выберите фото",
        "",
        "Изображения (*.jpg *.jpeg *.png *.bmp *.webp);;Все файлы (*)",
    )
    return path.strip() or None


def recognize_photo(camera_worker, image_path: str) -> List[Tuple[str, bytes, bytes]]:
    """Запустить ALPR по файлу изображения. Возвращает список (plate_text, full_image_bytes, crop_image_bytes)."""
    return camera_worker.recognize_image_file(image_path)
