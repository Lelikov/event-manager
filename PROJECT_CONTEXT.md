# Project Context: event-manager

## Overview
`event-manager` — это ingress-микросервис для приёма событий из нескольких источников (CloudEvents, frontend, backend),
валидации целостности/подлинности и публикации нормализованных CloudEvents в RabbitMQ.

После рефакторинга сервис приведён к стилю `calendar-bot`:
- интерфейсно-ориентированная архитектура,
- явное разделение по модулям (interfaces/controllers/adapters/routes/ioc),
- DI на базе **Dishka** с управлением scope,
- тонкие HTTP-роуты и бизнес-логика в контроллерах.

## Runtime & Entrypoint
- `main.py` — экспортирует `app`.
- `event_manager/app.py`:
  - создаёт Dishka-контейнер (`AppProvider + FastapiProvider`),
  - поднимает `FastAPI`, подключает Dishka integration,
  - регистрирует роуты,
  - в lifespan подключается к RabbitMQ, гарантирует topology, закрывает ресурсы при shutdown.

## HTTP API
- `POST /event/cloudevents`
  - Принимает входящий CloudEvent,
  - валидирует структуру,
  - публикует как CloudEvent в RabbitMQ с routing key на основе правил роутинга.

- `POST /ingest/frontend`
  - Принимает `FreeFormIngestRequest` (source/type/payload),
  - требует JWT в заголовке `frontend_jwt_header`,
  - проверяет claims + digest payload,
  - публикует событие в RabbitMQ.

- `POST /ingest/backend`
  - Принимает CloudEvent или JSON object,
  - требует HMAC signature в заголовке `backend_signature_header`,
  - валидирует подпись,
  - нормализует payload и публикует событие.

- `GET /health`
  - Технический healthcheck.

## Module Structure
- `event_manager/interfaces/`
  - `ingest.py` — `IIngestController`
  - `publisher.py` — `ICloudEventPublisher`, `ITopologyManager`
  - `routing.py` — `IEventRouter`
  - `security.py` — `IFrontendJWTVerifier`, `IBackendSignatureVerifier`

- `event_manager/controllers/`
  - `ingest.py` — оркестрация всех ingest-сценариев, валидации и публикации.

- `event_manager/adapters/`
  - `publisher.py`
    - `CloudEventPublisher` — публикация в RabbitMQ через FastStream broker;
    - `RabbitTopologyManager` — декларация exchange/queue/bindings.

- `event_manager/`
  - `routes.py` — HTTP слой (маппинг доменных ошибок в HTTP коды),
  - `ioc.py` — Dishka provider graph,
  - `config.py` — `Settings` (pydantic-settings),
  - `routing.py` — правила и реализация event routing,
  - `security.py` — JWT/HMAC verifiers,
  - `errors.py` — доменные ошибки ingest-конвейера,
  - `schemas.py` — входные DTO для API.

## DI / IoC (Dishka)
`event_manager/ioc.py` (`AppProvider`) регистрирует:
- **Scope.APP**
  - `Settings`
  - FastStream `RabbitRouter` / `RabbitBroker` / `RabbitExchange`
  - `IEventRouter` -> `EventRouter`
  - `IFrontendJWTVerifier` -> `FrontendJWTVerifier`
  - `IBackendSignatureVerifier` -> `BackendSignatureVerifier`
  - `ICloudEventPublisher` -> `CloudEventPublisher`
  - `ITopologyManager` -> `RabbitTopologyManager`
- **Scope.REQUEST**
  - `IIngestController` -> `IngestController`

## Error Handling Contract
- Доменные ошибки (`BadRequestError`, `UnauthorizedError`, `ConfigurationError`) поднимаются в контроллерах.
- В `routes.py` выполняется централизованный перевод в HTTP:
  - `BadRequestError` -> `400`
  - `UnauthorizedError` -> `401`
  - `ConfigurationError` -> `500`

## Routing & Topology
- Routing key вычисляется через `EventRouter` по `source_pattern` + `type_pattern`.
- Топология RabbitMQ берётся из `Settings.topology_queues` (или рассчитывается из routing destinations).
- Exchange: topic, durable.

## Configuration
Основные переменные в `Settings`:
- RabbitMQ: `rabbit_url`, `rabbit_exchange`, `default_rabbit_destination`, `event_routing_rules`, `rabbit_topology_queues`
- Frontend auth: `frontend_jwt_verify_key`, `frontend_jwt_header`, `frontend_jwt_algorithm`, `frontend_jwt_issuer`, `frontend_jwt_audience`
- Backend auth: `backend_signature_secret`, `backend_signature_header`, `backend_signature_algorithm`
- Source/type defaults: `frontend_source/frontend_type`, `backend_source/backend_type`

## Conventions for Future Tasks
1. Сначала добавлять/расширять Protocol в `event_manager/interfaces`.
2. Реализацию класть в `controllers` (оркестрация) или `adapters` (интеграция/IO).
3. Все зависимости регистрировать в `AppProvider` с корректным scope.
4. Роуты держать максимально тонкими: валидация HTTP-уровня + делегирование в контроллер.
5. Новые бизнес-ошибки добавлять в `errors.py` и централизованно маппить в `routes.py`.
