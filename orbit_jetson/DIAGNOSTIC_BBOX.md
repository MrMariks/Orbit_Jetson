# Диагностика: рамки bbox и отображение кадра

## 1. Функция `read_frame()` (camera.py)

Полностью: метод `read_frame()` в `orbit_jetson/camera.py` (примерно строки 1160–1252).

### Где читаются `_last_draw_bboxes`

```python
# Строки ~1186–1191 (под lock):
with self._last_draw_lock:
    bboxes = list(self._last_draw_bboxes)
    confidences = list(self._last_draw_confidences)
    plate_pts = [list(pts) for pts in self._last_draw_plate_points]
    hist_bbox = list(self._bbox_history)
    hist_conf = list(self._conf_history)
```

Дальше по `bboxes` и `hist_bbox` считаются `smoothed_bboxes`. Отрисовка выполняется только если выполняется условие:

```python
if (DRAW_OBJECT_BOXES and bboxes) or smoothed_bboxes or plate_pts:
```

То есть хотя бы одно из: есть bbox’ы (и включены object boxes), есть сглаженные bbox’ы или есть точки полигонов.

### Где вызывается `cv2.rectangle`

- Рамка номера (с padding и цветом по уверенности): строка ~1222  
  `cv2.rectangle(frame, (x1_p, y1_p), (x2_p, y2_p), color, 2)`
- Подложка под подпись «XX%»: строка ~1226  
  `cv2.rectangle(frame, (x1_p, ly1), (lx2, y1_p), (0, 0, 0), -1)`
- Оранжевые боксы детектора (если `DRAW_OBJECT_BOXES`): строки ~1229–1230  
  `cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 120, 0), 2)`
- Полигоны номеров: `cv2.polylines` (не rectangle), строки ~1232–1234.

Итог: если `bboxes` и `smoothed_bboxes` пустые и `plate_pts` пустой, блок с `cv2.rectangle` не выполняется — рамки не рисуются.

---

## 2. Подключение `frame_ready` и отображение в QLabel

### Сигнал и поток

- В `ui.py` класс `CameraThread` (QThread):
  - Сигнал: `frame_ready = pyqtSignal(object)` (строка ~533).
  - В `run()` в цикле вызывается `ok, data = self._camera.read_frame()` (строка ~552); при `ok and data` вызывается `self.frame_ready.emit(data)` (строка ~563).  
  То есть в сигнал уходят JPEG-байты того кадра, который уже прошёл через `read_frame()` (включая отрисовку bbox, если она была).

### Подключение к UI

- В `MainWindow._on_start()` (строка ~990):  
  `self._camera_thread.frame_ready.connect(self._on_frame)`
- Приём кадра: `_on_frame(self, jpeg_bytes)` (строка ~1057) только сохраняет байты:  
  `self._latest_frame_bytes = jpeg_bytes`
- Отображение: по таймеру вызывается `_paint_latest_frame()` (строка ~1061), который делает:
  - `img.loadFromData(QByteArray(self._latest_frame_bytes))`
  - `self._video_label.setPixmap(QPixmap.fromImage(img).scaled(...))` (строки ~1068–1069).

Виджет видео — `self._video_label` (QLabel), создаётся в `_build_ui()` (строка ~650), objectName `"videoLabel"`.

Итог: цепочка корректная — кадр из `read_frame()` (уже с нарисованными bbox) попадает в `frame_ready` → `_on_frame` → `_latest_frame_bytes` → `_paint_latest_frame` → `_video_label`. Если рамок нет на экране, значит в `read_frame()` либо не попадаем в блок отрисовки (пустые `bboxes`/`smoothed_bboxes`/`plate_pts`), либо они не заполняются из ALPR.

---

## 3. Логи «ALPR: запись _last_draw_bboxes» и «Отрисовка рамки bbox»

В приложённом логе терминала (1.txt) этих строк **нет**.

Причина: раньше сообщения шли через `logger.debug(...)`. В `main.py` корневой логгер выставлен в `logging.CRITICAL`, отдельный handler с уровнем INFO настроен только для `"orbit_jetson.status"`. Логгер `orbit_jetson.camera` не выводил DEBUG в консоль.

**Сделано для диагностики:** сообщения переведены на `logger.info`, в `main.py` для логгера `orbit_jetson.camera` установлены уровень INFO и тот же StreamHandler (stdout). После перезапуска при наведении камеры на номер в консоли должны появиться:
- «ALPR: запись _last_draw_bboxes …» — при записи в `_last_draw_bboxes`;
- «Отрисовка рамки bbox: (x1_p, y1_p, x2_p, y2_p)» — при каждом вызове `cv2.rectangle` для рамки.

Если видите только первое и не видите второго — данные до отрисовки доходят, но условие в `read_frame()` не выполняется в тот же момент (например, другой поток очистил данные). Если не видите ни того ни другого — до записи в `_last_draw_bboxes` данные не доходят (ALPR не детектирует или не пишет в `_last_draw_*`).
