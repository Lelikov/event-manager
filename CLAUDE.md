# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`event-receiver` is an ingress microservice for receiving incoming events, validating authorization/integrity, and publishing normalized CloudEvents to RabbitMQ. The codebase is in the `event_receiver` package and follows interface-driven architecture with dependency injection via **Dishka**.

**Tech Stack**: Python 3.14, FastAPI, FastStream (RabbitMQ), Pydantic, structlog, CloudEvents

## Development Commands

### Environment Setup
```bash
# Install dependencies using uv
uv sync

# Activate virtual environment
source .venv/bin/activate
```

### Running the Application
```bash
# Run locally (requires RabbitMQ running)
uvicorn event_receiver.main:app --host 0.0.0.0 --port 8888 --log-config uvicorn_config.json

# Run with Docker Compose (includes RabbitMQ)
docker-compose up

# Build Docker image
docker build -t event-receiver .
```

### Code Quality
```bash
# Run linting and formatting with ruff
ruff check --fix .
ruff format .

# Run pre-commit hooks on all files
pre-commit run --all-files

# Install pre-commit hooks
pre-commit install
```

### Configuration
- Copy `.env` from `.env.example` if needed
- Required environment variables: see `event_receiver/config.py` `Settings` class
- Key configs: JWT verification keys, RabbitMQ URL, API keys for integrations

## Architecture

### Interface-Driven Design
The codebase separates contracts from implementations:

- **Interfaces** (`event_receiver/interfaces/`): Python `Protocol` classes defining contracts
  - `IIngestController` - orchestrates event ingestion
  - `ICloudEventPublisher` - publishes events to RabbitMQ
  - `ITopologyManager` - manages RabbitMQ topology (exchanges/queues/bindings)
  - `IEventRouter` - resolves routing keys for events
  - `IAuthorizationJWTVerifier` - verifies JWT tokens

- **Controllers** (`event_receiver/controllers/`): Business logic orchestration
  - `IngestController` - validates, transforms, and publishes incoming events

- **Adapters** (`event_receiver/adapters/`): Infrastructure integrations
  - `CloudEventPublisher` - publishes CloudEvents to RabbitMQ via FastStream
  - `RabbitTopologyManager` - declares RabbitMQ topology

- **Routes** (`event_receiver/routes.py`): Thin HTTP layer that delegates to controllers

### Dependency Injection (Dishka)

All dependencies are registered in `event_receiver/ioc.py` (`AppProvider`):

- **Scope.APP**: Singletons (Settings, Broker, Publishers, Routers, etc.)
- **Scope.REQUEST**: Per-request instances (IngestController)

When adding new dependencies:
1. Define the Protocol in `interfaces/`
2. Implement in `controllers/` or `adapters/`
3. Register in `AppProvider` with appropriate scope
4. Inject via constructor

### Event Ingestion Flow

1. HTTP request → `routes.py` endpoint
2. Auth validation: JWT (jitsi), HMAC signatures (unisender-go, getstream, calcom) or static API keys (booking, admin)
3. Parse/transform event into CloudEvent format
4. `IngestController` orchestrates validation and publishing
5. `EventRouter` resolves routing key based on event source/type patterns
6. `CloudEventPublisher` publishes to RabbitMQ exchange
7. HTTP layer maps domain errors to appropriate status codes

### HTTP Endpoints

- `POST /event/booking` - Booking service CloudEvents with API key authorization (constant-time)
- `POST /event/calcom` - Native cal.com webhooks with `X-Cal-Signature-256` HMAC-SHA256 validation
- `POST /event/jitsi` - Jitsi CloudEvents with JWT authorization (signature + claims)
- `POST /event/unisender-go` - UniSender Go webhooks with MD5 signature validation
- `POST /event/getstream` - GetStream webhooks with HMAC-SHA256 signature validation (inline, constant-time)
- `POST /event/admin` - Admin CloudEvents (`user.email.*`, `booking.client_reassigned`) with `Authorization: Bearer <key>` (constant-time)
- `GET /health` - Liveness probe (no dependency calls)
- `GET /ready` - Readiness probe (RabbitMQ connection ping; 503 when down)
- `GET /metrics` - Prometheus exposition (`metrics.py`): HTTP RED (`http_requests_total`, `http_request_duration_seconds` by route template), `receiver_webhooks_total{source,result}`, `receiver_publish_failures_total{reason}`, `receiver_unknown_event_types_total{source}`

### Error Handling Pattern

Domain errors are raised in controllers (`event_receiver/errors.py`):
- `BadRequestError` → HTTP 400
- `UnauthorizedError` → HTTP 401
- `ConfigurationError` → HTTP 500
- `PublishUnavailableError` → HTTP 503 (broker confirm timeout or unroutable return; webhook sources retry)

Centralized HTTP mapping in `routes.py` ensures consistent error responses.

### Event Routing

Routing logic in `event_receiver/routing.py`:
- Rules match events by glob patterns on `source` and `type` fields
- First matching rule determines the routing key
- Fallback to `default_rabbit_destination` if no match
- Routing rules configured in `Settings.event_routing_rules`

**See `QUEUES_DIGEST.md`** for current routing rules and queue mappings.

**See `EVENTS_DIGEST.md`** for event schemas and payload structures.

### RabbitMQ Topology

- **Exchange**: topic exchange (durable), name from `Settings.rabbit_exchange`
- **Queues**: auto-created on startup via `ITopologyManager.ensure_topology()`
  - Derived from routing destinations unless explicitly set in `Settings.rabbit_topology_queues`
  - Each queue bound to exchange with routing key = queue name
- **CloudEvents Binary Format**: Events published with CloudEvent headers (`ce-*`) and data as message body

### Logging

Structured logging via `structlog` (`event_receiver/logger.py`):
- Configure on startup in `main.py` lifespan
- Log level from `Settings.log_level`
- Console rendering enabled in debug mode
- Log key events: startup/shutdown, HTTP requests, business operations, errors

## Development Conventions

### Adding a New Ingest Endpoint

1. Define request/response models if needed (or use CloudEvent directly)
2. Add security verification logic in `event_receiver/security.py` if new auth method
3. Create controller method in `IngestController` (or new controller)
4. Add Protocol to `interfaces/` and register in DI if needed
5. Add route in `routes.py` with error mapping
6. Add routing rules to `config.py` default rules or configure via env

### Adding New Event Types

1. Update `EVENTS_DIGEST.md` with schema
2. Add routing rule to `_default_route_rules()` in `config.py` if needed
3. Update `QUEUES_DIGEST.md` with routing destination

### Modifying RabbitMQ Topology

- Add/modify routing rules in `Settings.event_routing_rules`
- Queues are auto-derived from destinations or set explicitly in `Settings.rabbit_topology_queues`
- Topology is ensured on application startup

### Code Style

- Python 3.14+ (uses modern syntax)
- Ruff for linting/formatting (config in `pyproject.toml`)
- Type hints required (Protocol for interfaces, concrete types for implementations)
- Structured logging with context (not print statements)
- Pre-commit hooks enforce style

## Reference Documentation

- `PROJECT_CONTEXT.md` - Detailed architectural documentation
- `EVENTS_DIGEST.md` - Event schemas and payloads
- `QUEUES_DIGEST.md` - RabbitMQ routing rules and queue mappings
- `pyproject.toml` - Dependencies and tool configuration

## Service Documentation

- `docs/SERVICE_OVERVIEW.md` — architecture, maturity, known issues
- `docs/API_CONTRACTS.md` — HTTP endpoints, request/response schemas
- `docs/DEPENDENCIES.md` — external service dependencies and failure modes
- `docs/AUDIT.md` — audit findings for this service

Cross-service architecture docs (message contracts, system topology, onboarding) are in `../docs/`.

## Documentation Requirements

All code changes MUST include corresponding documentation updates:
- New/changed endpoints → update `docs/API_CONTRACTS.md` and `PROJECT_CONTEXT.md`
- New/changed event types or routing rules → update `QUEUES_DIGEST.md` and `EVENTS_DIGEST.md`
- New/changed dependencies or external service calls → update `docs/DEPENDENCIES.md`
- Architectural changes (DI, interfaces, adapters) → update `PROJECT_CONTEXT.md` and `docs/SERVICE_OVERVIEW.md`
- Bug fixes for audit findings → update `docs/AUDIT.md`

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
|------|----------|
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
