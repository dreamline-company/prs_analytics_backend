# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Setup:**
```bash
uv sync                      # install dependencies
. .venv/bin/activate         # activate venv
```

**Run locally (outside Docker):**
```bash
# All commands that import project code must run from src/ (Python path root)
cd src && uvicorn entrypoints.server:app --reload
```

**Migrations (run from `src/`):**
```bash
cd src && alembic upgrade head
cd src && alembic revision --autogenerate -m "description"
```

**Lint:**
```bash
ruff check src/
ruff format src/
```

**Deploy via Docker:**
```bash
make up-redis      # start Redis
make up-api        # start FastAPI + uvicorn
make up-celery     # start Celery beat + Flower
make logs-api TAIL=100 FOLLOW=1
```

**Run a task script directly:**
```bash
cd src && python -m apps.wells.tasks.load_wells.load_wells
```

## Architecture

### Python path root

All imports are absolute from `src/`. The `src/` directory is the Python root:
```python
from apps.kpi.use_cases.get_main_kpi_status_updates import GetMainKpiStatusUpdates
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec
from core.settings import get_settings
```

### App structure

Each app under `src/apps/{app_name}/` follows this layout:
```
dto/
  commands/       # write-scenario input schemas
  queries/        # read-scenario query objects
  requests/       # HTTP/WS incoming schemas
  responses/      # HTTP/WS outgoing schemas
  internal/       # internal DTOs (e.g. repository create/update schemas)
    repositories/
models/           # SQLAlchemy Mapping ORM models
repositories/     # AsyncAlchemyRepository subclasses
routers/
  api/v1/         # FastAPI HTTP routers
  ws/v1/          # FastAPI WebSocket routers
tasks/            # Celery/standalone async task classes
use_cases/        # one class per business scenario
services/         # shared service classes (broader than use cases)
```

Dependency direction: `router → use_case → repository/service → database/external`

### Repository pattern

All repositories extend `AsyncAlchemyRepository[CreateDTO, UpdateDTO, ModelT]` from `shared/repository/sqlalchemy.py`. Use `QuerySpec` for model queries and `ProjectionQuerySpec` for DTO/aggregate queries. Repositories never commit — transaction control belongs to the caller (task or service layer).

### Database sessions

`shared/database/sql/setup.py` exports `session_makers` dict. Keys: `"app"` (PostgreSQL, main DB), `"abai"` (MySQL), `"kainar"` / `"dmg"` (MSSQL via aioodbc/WinCC). Use `shared/dependencies/db.py` FastAPI dependency `get_app_session` for HTTP routes.

### External integrations

- **ABAI** (MySQL) — source of truth for wells, repair work types, coordinates: `shared/integrations/abai/`
- **WinCC / Kainar / DMG** (MSSQL via ODBC) — telemetry data: `shared/integrations/wincc/`
- **S3 / MinIO** — dynamograms, SPO files, reports: `shared/database/s3/`

### Celery tasks

Tasks live in `{app}/tasks/{task_name}/` as standalone async classes with a `.run()` method. They can be executed directly via `asyncio.run(main())` or scheduled via Celery beat. Beat schedule is configured in `src/entrypoints/celery.py`.

### Models registry

`src/apps/models_registry.py` imports all ORM models so Alembic autogenerate picks them up. Add new models here.

### Settings

`src/core/settings/base.py` defines `Settings` (pydantic-settings). Read from `src/.env`. Required env vars include `ENV`, `APP_ASYNC_DATABASE_URL`, `APP_SYNC_DATABASE_URL`, `ABAI_ASYNC_DATABASE_URL`, `KAINAR_*`, `DMG_*`, `REDIS_*`, `S3_*`, `LLM_API_KEY`, `LLM_MODEL_NAME`.

### AI assistant

LangGraph/LangChain logic is isolated in `src/apps/ai_assistant/ai/`. The chat assistant uses a LangGraph agent (`agent.py`) with tools (`tools.py`). AI use cases must not be invoked from routers directly — route through `use_cases/`.
