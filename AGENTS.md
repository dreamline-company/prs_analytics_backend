# AGENTS.md

## Project overview

Бэкенд-платформа для аналитики эффективности ПРС работ.

- **Пользователи:** оператор, диспетчер
- **Тип приложения:** monolith backend
- **Стек:** FastAPI, LangChain, LangGraph, Pydantic, SQLAlchemy
- **Архитектура:** app-based + use cases
- **Основной язык проекта:** Python
- **Язык коммуникации в кодовой базе:** русский для бизнес-терминов, английский для кода, API, классов, функций и технических сущностей

## Product context

Система предназначена для анализа эффективности ПРС работ, обработки данных, расчёта KPI, построения аналитических сценариев и поддержки AI-ассистента.


При внесении изменений важно сохранять доменную терминологию и не заменять бизнес-понятия абстрактными техническими названиями без необходимости.

## Repository structure

```text
src/
  core/             # инфраструктурная логика всего проекта
  entrypoints/      # точки входа: запуск API, workers, cli и т.п.
  shared/           # общий код, используемый несколькими apps
  apps/             # основные бизнес-приложения backend
    {app_name}/
      dto/          # commands, internal(dto для внутренней логики services + usecases), queries, requests(API, WS), responses(API, WS)
      models/       # ORM-модели приложения
      routers/      # FastAPI routers
      use_cases/    # прикладные сценарии. UseCases должны быть 1 класс для 1го функционала
      services/    # общие сервис классы. Сервис более общий чем UseCase и может содержать в себе несколько бизнес логик




```

Code rules:
1. Типипзируй все. 
2. Используй Generics если нужно
3. Используй новые возможности Python3.12+ для типизации. К примеру `class Example[N, M]`. К прмеру `def example[N]() -> N:` и тд
4. SqlAlchemy: используй Mapping модели

Architectural rules
1. App-based boundaries

Каждое приложение в src/apps/{app_name} должно быть максимально изолированным.

Разрешено:

импортировать код из src/shared
импортировать инфраструктуру из src/core
использовать публичные DTO/use cases других apps только при явной необходимости

Не рекомендуется:

напрямую обращаться к внутренним модулям другого app
делать циклические зависимости между apps
размещать бизнес-логику в routers
размещать инфраструктурную логику в use cases
2. Use case first

Бизнес-сценарии должны находиться в use_cases.

Router должен:

принять request
провалидировать входные данные через Pydantic
вызвать use case
вернуть response

Router не должен:

содержать сложную бизнес-логику
напрямую собирать SQL-запросы
выполнять расчёты KPI
управлять LangGraph/LangChain сценариями напрямую

Пример предпочтительного направления зависимости:

router -> use_case -> repository/service -> database/external provider
3. DTO separation

Используй разные DTO для разных слоёв, если это повышает ясность:

requests/ — входящие HTTP-схемы
responses/ — исходящие HTTP-схемы
internal/ — внутренние DTO приложения
commands/ — команды для write-сценариев
queries/ — query-объекты для read-сценариев

Не смешивай ORM-модели и API-схемы.

4. Core vs Shared

src/core/ — инфраструктура всего проекта:

настройки
DI/container
database/session
logging
security
конфигурация FastAPI
integrations bootstrap
LangChain/LangGraph infrastructure

src/shared/ — переиспользуемый код без привязки к конкретному app:

base schemas
общие exceptions
common utils
pagination
result objects
value objects
общие типы

Если код содержит бизнес-правила конкретного app, он не должен попадать в shared.

Coding guidelines
Python
Используй Python type hints.
Предпочитай явные типы возвращаемых значений.
Не используй Any без необходимости.
Не оставляй неиспользуемые импорты.
Не добавляй глобальное состояние без причины.
Предпочитай маленькие классы и функции с одной ответственностью.
Для асинхронного кода используй async/await последовательно.
Naming

Кодовые сущности называй на английском:

CalculateKpiUseCase
GetWellOperationsQuery
CreateReportCommand
PrsEfficiencyService

Доменную терминологию можно сохранять в названиях, если она устойчива в проекте:

PrsOperation
PrsWork
WellDowntime
CrewEfficiency

Не используй слишком общие названия:

Manager
Handler
Processor
DataService
Helper

Если используешь такие имена, уточняй контекст:

KpiCalculationService
ReportGenerationHandler
WellOperationProcessor
Imports

Предпочитай абсолютные импорты от src.

from src.apps.kpi.use_cases.calculate_kpi import CalculateKpiUseCase
from src.shared.pagination import PaginationParams

Не создавай относительные импорты с большим количеством ...

Error handling
Не подавляй исключения через пустой except.
Для ожидаемых бизнес-ошибок используй доменные exceptions.
Для инфраструктурных ошибок используй отдельные exception-типы.
Не возвращай None вместо ошибки, если отсутствие значения является ошибочным состоянием.
Ошибки API должны быть понятны оператору/диспетчеру, но не раскрывать внутренние детали системы.
FastAPI rules

Router должен быть тонким.

Пример структуры:

@router.post("/kpi/calculate", response_model=CalculateKpiResponse)
async def calculate_kpi(
    request: CalculateKpiRequest,
    use_case: CalculateKpiUseCase = Depends(get_calculate_kpi_use_case),
) -> CalculateKpiResponse:
    result = await use_case.execute(request.to_command())
    return CalculateKpiResponse.from_result(result)

Правила:

response_model указывать явно.
HTTP-специфичные детали держать в router.
Use case не должен зависеть от FastAPI Request, Depends, HTTPException.
Валидацию формата делать в Pydantic-схемах.
Бизнес-валидацию делать в use case или domain service.
SQLAlchemy rules
ORM-модели держать отдельно от Pydantic-схем.
Не возвращать ORM-модели напрямую из API.
Не выполнять database commit внутри router.
Управление транзакциями должно быть централизовано.
Избегать N+1 запросов.
Для сложных запросов выделять repository/query service.

Пример направления:

use_case -> repository -> SQLAlchemy session
Pydantic rules
Используй Pydantic для API-контрактов и DTO.
Не добавляй бизнес-логику в Pydantic-модели, кроме простой валидации.
Для response-схем избегай утечки внутренних полей.
Для денежных, временных и числовых метрик используй точные типы, где это важно.
LangChain / LangGraph rules

AI-логику держать изолированно внутри соответствующего app, например:

src/apps/ai_assistant/
  ai/
  dto/
  use_cases/
  routers/

Правила:

Не смешивать AI orchestration с API routers.
Prompt templates хранить отдельно от бизнес-логики.
LangGraph nodes должны быть маленькими и тестируемыми.
Состояние graph должно быть явно типизировано.
Интеграции с LLM должны быть обёрнуты в сервисы или providers.
Не хардкодить ключи, модели, temperature и provider-настройки в use cases.
Configuration
Все настройки брать из config/env.
Не хардкодить secrets.
Не коммитить .env.
Для новых настроек добавлять типизированное поле в settings.
Значения по умолчанию должны быть безопасными для локальной разработки.
Testing expectations

При изменении бизнес-логики добавляй или обновляй тесты.

Покрывать тестами:

use cases
domain services
repositories/query services при сложной логике
LangGraph node logic
API contracts при изменении endpoint behavior

Не обязательно тестировать:

простые Pydantic DTO без логики
тривиальные wiring-функции
boilerplate

Тесты должны быть детерминированными. LLM-вызовы, внешние API и database side effects нужно мокать или изолировать.

Before making changes

Перед изменениями:

Определи app, к которому относится задача.
Проверь существующие DTO, use cases, routers и models.
Сохрани текущий стиль проекта.
Не делай масштабный рефакторинг без необходимости.
Не меняй публичные API-контракты без явной причины.
When adding a new feature

Для новой функциональности используй такой порядок:

Создай или обнови DTO/request/response.
Добавь use case.
Добавь repository/service, если нужен доступ к данным или внешним системам.
Добавь router endpoint.
Зарегистрируй зависимости.
Добавь тесты.
Обнови документацию, если меняется поведение API.
When editing existing code
Минимизируй diff.
Не переименовывай сущности без необходимости.
Сохраняй обратную совместимость.
Не меняй формат response без явного требования.
Не перемещай файлы, если задача этого не требует.
Если замечаешь unrelated-проблему, не исправляй её автоматически — оставь комментарий в ответе.
API design
Endpoint paths должны быть понятными и стабильными.
Для read-операций используй GET.
Для создания — POST.
Для полного обновления — PUT.
Для частичного обновления — PATCH.
Для удаления — DELETE.
Для action-like операций допустим POST, например /reports/{id}/generate.

Response должен быть предсказуемым и типизированным.

Domain logic

Бизнес-правила эффективности ПРС должны быть явно выражены в коде.

Не прячь важные правила в:

SQL без пояснений
prompt text
router
anonymous lambda
generic helper

Если формула KPI или расчёт важны, вынеси их в именованную функцию, service или value object.

Comments and documentation

Комментарии нужны, когда код объясняет бизнес-правило, ограничение или нетривиальное решение.

Не добавляй комментарии, которые просто повторяют код.

Хорошо:

# Downtime below threshold is ignored according to dispatching rules.

Плохо:

# Increment counter by one.
Security
Не логируй secrets, tokens, passwords.
Не возвращай stack trace пользователю.
Проверяй права доступа на уровне use case или dependency.
Не доверяй входным данным.
Валидируй file uploads, external URLs и любые данные от пользователя.
Для AI-инструментов явно ограничивай доступные действия.
Logging

Логи должны помогать расследовать проблемы в эксплуатации.

Логируй:

старт и завершение важных сценариев
идентификаторы задач/отчётов/операций
ошибки интеграций
длительные операции
сбои AI orchestration

Не логируй:

персональные данные без необходимости
secrets
большие payload целиком
prompt с чувствительными данными
Performance
Избегай лишних database round-trips.
Для больших выборок используй pagination.
Не загружай все записи в память без необходимости.
Для аналитических расчётов учитывай объём данных.
Для долгих операций используй background jobs, если это предусмотрено архитектурой.
Codex instructions

При работе с этим репозиторием:

Отвечай на русском, если пользователь пишет на русском.
Код, имена файлов, классов, функций и переменных пиши на английском.
Сначала изучай существующую структуру app перед добавлением новых файлов.
Следуй текущему стилю проекта.
Не добавляй новые зависимости без необходимости.
Не меняй архитектуру без явного запроса.
Не делай speculative changes.
Не создавай mock-реализации вместо полноценного решения, если пользователь просит production-код.
Если информации недостаточно, выбери наиболее локальное и безопасное изменение.
После изменений кратко перечисли, что было изменено и какие проверки стоит запустить.
Quality checklist

Перед завершением задачи проверь:

код находится в правильном app
router не содержит бизнес-логику
use case не зависит от FastAPI
DTO не смешаны с ORM-моделями
типы указаны явно
ошибки обработаны осмысленно
нет secrets и hardcoded config
нет лишних зависимостей
тесты добавлены или обновлены при изменении бизнес-логики
публичные API-контракты не сломаны случайно
Useful commands



Если конкретные инструменты не настроены в проекте, не добавляй их автоматически без отдельного запроса.


# COMMANDS:
   - `. .venv/bin/activate` - запуск питона
