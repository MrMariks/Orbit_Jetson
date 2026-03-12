# -*- coding: utf-8 -*-
# DEBUG ONLY — удалить при релизе.
# Логика загрузки видео для отладки: выбор файла и установка источника в CameraWorker.

from typing import Optional

from PyQt5.QtWidgets import QFileDialog, QWidget


def get_video_path(parent: Optional[QWidget] = None) -> Optional[str]:
    """Открыть диалог выбора видеофайла. Возвращает путь или None."""
    path, _ = QFileDialog.getOpenFileName(
        parent,
        "Выберите видеофайл",
        "",
        "Видео (*.mp4 *.avi *.mov *.mkv *.webm);;Все файлы (*)",
    )
    return path.strip() or None


def clear_video_source(camera_worker) -> None:
    """Сбросить источник видео — вернуться к камере."""
    camera_worker.set_video_path(None)


def set_video_source(camera_worker, path: str) -> None:
    """Установить источник видео по пути к файлу."""
    camera_worker.set_video_path(path)
