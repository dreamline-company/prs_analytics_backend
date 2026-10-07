# Детектор обрыва штанги — правило R2

Изолированный слайс: всё, что касается детекции обрыва штанги (Rod_break), живёт
здесь. Другие детекторы добавляются соседними папками в `apps/detectors/`.

## Физика и правило (spec 5.2)

После обрыва вал двигателя крутится без нагрузки: момент проваливается почти в
ноль, а скорость сохраняется. Именно пара «ноль момента + крутящаяся скорость»
отличает аварию от штатной остановки, где обнуляется и то и другое.

```
flag = (mom_min < 0.4 * mom_med24) AND (spd > 0.4 * spd_med24)
срабатывание = flag в 2 подряд 2-часовых корзинах; дата = первая корзина серии.
окно анализа = 60 дней.
```

## Данные

- Источник — app-копия телеметрии `telemetry_sdmo_fc_data` (`SdmoFcData`,
  Postgres), **не** sdmo MySQL напрямую.
- Флот — станции `type_1900 = 6` (Danfoss VLT, ЭВН-КУДУ).
- Момент — регистр addr **1991** («Момент штанги»), скорость — addr **1998**
  («Скорость ротора»); ключи JSON-карты `SdmoFcData.data`.
- Ряд внутрисуточный (~1 отсчёт / 2 мин). Агрегация в 2-часовые корзины (MIN
  момента, медиана скорости) выполняется в SQL по `savetime` (явный UTC).
- Выбросы момента (Int32-глюки) отсекаются диапазоном `MOMENT_CLIP_*` до MIN.

## Слои

```
rule.py            чистое ядро (bucket → flag), без I/O — тестируется отдельно
config.py          все пороги и параметры
services/
  telemetry_source SQL: 2ч-корзины MIN(момент)/медиана(скорость) из app-копии
  bucketizer       скользящая медиана за 24ч (гэп-устойчивое трейлинг-окно)
  failure_dt       восстановление даты отказа + классификация события
detector.py        сборка: source → bucketizer → rule → failure_dt → результат
use_cases/         run_for_well, list_detections
tasks/run_detection ежедневный прогон флота (standalone + celery-обёртка)
routers/           GET /api/detectors/v1/rod-breaks/detections
models/            RodBreakRun, RodBreakDetection (результаты в БД app)
```

## Запуск

```bash
# разовый прогон вручную
cd src && python -m apps.detectors.rod_breaks.tasks.run_detection.run_detection

# по расписанию: celery beat, задача detectors.rod_breaks.run (ежедневно 06:00)

# тесты чистого ядра (без БД)
cd src && python -m pytest apps/detectors/rod_breaks/tests -q
```

## Проверка эпизодов (ложная тревога или нет)

`verification/` — независимая от правила проверка: после срабатывания смотрит
ремонты и статусы ABAI, статус привода СДМО (регистр 1999) и замеры нефти ЦИТС
и ставит эпизоду отметку в `detectors_verification` (журнал смен —
`detectors_verification_history`). Таска `detectors.rod_breaks.verify`
ежечасно в :30. Пороги — `verification/config.py`, логика без I/O —
`verification/rule.py`, описание простым языком — `docs/r2_verification.md`.

```bash
cd src && python -m apps.detectors.rod_breaks.tasks.verify.verify --dry-run
cd src && python -m apps.detectors.rod_breaks.tasks.verify.verify --backfill-days 60
```

## Не сделано (следующие шаги)

- Бэкфилл 60 дней в `telemetry_sdmo_fc_data` — детектор считает по загруженной
  копии; без данных вернёт пусто.
- Бэктест на журнале ремонтов (цель recall ≈ 82%, 79/96).
- Нормировка порогов под слабые скважины (база момента < 50): сейчас глобальный
  0.4 + флаг `low_confidence`.
