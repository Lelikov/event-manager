# Project Context: event-manager

## Overview
`event-manager` — ingress-микросервис для приёма входящих событий,
валидации авторизации/целостности и публикации нормализованных CloudEvents в RabbitMQ.

Текущая кодовая база реализована в пакете `event_receiver` и построена в стиле
interface-driven архитектуры:
- контракты через `Protocol` в `interfaces`,
- orchestration в `controllers`,
- инфраструктурные адаптеры в `adapters`,
- thin HTTP routes,
- DI через **Dishka**.

## Runtime & Entrypoint
- `event_receiver/main.py`:
  - создаёт Dishka container (`AppProvider + FastapiProvider`),
  - на lifespan инициализирует logging (`structlog`),
  - подключает RabbitMQ broker,
  - гарантирует Rabbit topology через `ITopologyManager`,
  - закрывает broker и container при shutdown.

## Logging
- Логирование стандартизировано через `structlog` (`event_receiver/logger.py`).
- Используются структурированные события с контекстом в ключевых зонах:
  - startup/shutdown и инфраструктурная инициализация (`main.py`, `ioc.py`),
  - HTTP ingest flow и error mapping (`routes.py`),
  - бизнес-шаги ingest-пайплайна (`controllers/ingest.py`),
  - security-проверки JWT (`security.py`),
  - routing resolution и Rabbit publish/topology (`routing.py`, `adapters/publisher.py`).

## HTTP API
- `POST /event/cloudevents`
  - принимает CloudEvent,
  - ожидает JWT в `Authorization`,
  - проверяет подпись JWT,
  - парсит CloudEvent,
  - сверяет claims (`source`, `type`) с event,
  - публикует событие в RabbitMQ.

- `POST /event/unisender-go`
  - принимает JSON payload UniSender Go,
  - валидирует подпись в поле `auth` (MD5 от payload c подстановкой `email_api_key`),
  - при успехе публикует нормализованное событие в RabbitMQ.

- `GET /event/cloudevents`, `GET /event/unisender-go`
  - lightweight endpoint-health ответ `{"status": "ok"}`.

- `GET /health`
  - технический healthcheck `{"status": "ok"}`.

## Module Structure
- `event_receiver/interfaces/`
  - `ingest.py` — `IIngestController`
  - `publisher.py` — `ICloudEventPublisher`, `ITopologyManager`
  - `routing.py` — `IEventRouter`
  - `security.py` — `IAuthorizationJWTVerifier`

- `event_receiver/controllers/`
  - `ingest.py` — ingest orchestration и валидации.

- `event_receiver/adapters/`
  - `publisher.py`
    - `CloudEventPublisher` — публикация CloudEvents в RabbitMQ;
    - `RabbitTopologyManager` — декларация exchange/queue/bindings.

- `event_receiver/`
  - `routes.py` — HTTP слой и маппинг доменных ошибок в HTTP,
  - `ioc.py` — Dishka provider graph,
  - `config.py` — `Settings` (pydantic-settings),
  - `routing.py` — routing rules/logic,
  - `security.py` — JWT verifier,
  - `errors.py` — доменные ошибки ingest-конвейера,
  - `logger.py` — конфигурация structlog,
  - `schemas.py` — placeholder под будущие DTO.

## DI / IoC (Dishka)
`event_receiver/ioc.py` (`AppProvider`) регистрирует:
- **Scope.APP**
  - `Settings`
  - FastStream `RabbitRouter` / `RabbitBroker` / `RabbitExchange`
  - `IEventRouter` -> `EventRouter`
  - `IAuthorizationJWTVerifier` -> `AuthorizationJWTVerifier`
  - `ICloudEventPublisher` -> `CloudEventPublisher`
  - `ITopologyManager` -> `RabbitTopologyManager`
- **Scope.REQUEST**
  - `IIngestController` -> `IngestController`

## Error Handling Contract
- Доменные ошибки (`BadRequestError`, `UnauthorizedError`, `ConfigurationError`) поднимаются в контроллере.
- Централизованный HTTP mapping в `routes.py`:
  - `BadRequestError` -> `400`
  - `UnauthorizedError` -> `401`
  - `ConfigurationError` -> `500`

## Routing & Topology
- Routing key вычисляется через `EventRouter` по `source_pattern` + `type_pattern`.
- При отсутствии match используется `default_rabbit_destination`.
- Topology queues берутся из `Settings.topology_queues`
  (явно заданные `rabbit_topology_queues` или auto-derived из routing destinations).
- Exchange: topic, durable.

## Configuration
Ключевые настройки в `Settings`:
- Общие: `debug`, `log_level`
- RabbitMQ: `rabbit_url`, `rabbit_exchange`, `default_rabbit_destination`, `event_routing_rules`, `rabbit_topology_queues`
- JWT authorization: `authorization_jwt_verify_key`, `authorization_jwt_algorithm`, `authorization_jwt_issuer`, `authorization_jwt_audience`
- UniSender Go: `email_api_key`

## Conventions for Future Tasks
1. Сначала добавлять/расширять Protocol в `event_receiver/interfaces`.
2. Реализацию класть в `controllers` (оркестрация) или `adapters` (интеграция/IO).
3. Все зависимости регистрировать в `AppProvider` с корректным scope.
4. Роуты держать максимально тонкими: HTTP-валидация + делегирование в контроллер.
5. Новые бизнес-ошибки добавлять в `errors.py` и централизованно маппить в `routes.py`.
