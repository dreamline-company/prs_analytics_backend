# PRS Effectiveness Backend

Бэкенд-платформа для аналитики эффективности ПРС работ. Проект предназначен для обработки данных по скважинам и ремонтам, расчета KPI, работы аналитических сценариев и поддержки AI-ассистента.

## Стек

- Python 3.12
- FastAPI
- SQLAlchemy
- Pydantic
- Celery
- Redis
- LangChain / LangGraph
- Docker Compose

## Структура проекта

```text
src/
  apps/          # бизнес-приложения: KPI, wells, repairs, telemetry, files, AI assistant
  core/          # настройки и инфраструктура проекта
  entrypoints/   # точки входа API и Celery
  shared/        # общий переиспользуемый код
deploy/
  app/           # Docker Compose и Dockerfile для API/Celery
  redis/         # Docker Compose для Redis
```

## Локальная подготовка и локальный запуск

Для создания python окружения установите на пк uv затем:

```bash  
uv sync
```

Для запуска Python-команд активируйте виртуальное окружение:

```bash
. .venv/bin/activate
```

Перед запуском контейнеров должен быть заполнен файл окружения:

```text
src/.env
```

Он используется Docker Compose через параметр `ENV_FILE` в `makefile`.

## Деплой через makefile

Все команды запускаются из корня репозитория.

Посмотреть доступные команды:

```bash
make help
```

Запустить Redis:

```bash
make up-redis
```

Запустить API:

```bash
make up-api
```

Запустить Celery beat и Flower:

```bash
make up-celery
```

Остановить сервисы:

```bash
make down-api
make down-celery
make down-redis
```

Посмотреть логи:

```bash
make logs-api
make logs-celery
make logs-redis
```

Для управления размером вывода и режимом follow можно передать переменные:

```bash
make logs-api TAIL=100 FOLLOW=1
```

По умолчанию используются:

- `PROJECT_NAME=prs_effectiveness`
- `ENV_FILE=src/.env`
- `TAIL=500`
- `FOLLOW=0`

При необходимости их можно переопределить при запуске команды:

```bash
make up-api ENV_FILE=src/.env PROJECT_NAME=prs_effectiveness
```

## Сервисы

- API запускается через `uvicorn entrypoints.server:app` и слушает порт `8000` внутри контейнера. Внешний порт задается переменной `API_PORT`.
- Redis запускается в отдельном compose-файле и использует переменные `REDIS_EXTERNAL_PORT`, `REDIS_USER`, `REDIS_USER_PASSWORD`.
- Celery compose запускает `celery_beat` и Flower. Flower доступен на внешнем порту `5556`.
