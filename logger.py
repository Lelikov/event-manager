import logging
from collections.abc import Callable, Iterable
from functools import partial

import structlog
import ujson
from structlog.processors import JSONRenderer


def setup_logger(log_level: int, console_render: bool) -> None:
    shared_processors = [
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.contextvars.merge_contextvars,
        structlog.processors.CallsiteParameterAdder(
            {
                structlog.processors.CallsiteParameter.PATHNAME,
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.MODULE,
                structlog.processors.CallsiteParameter.FUNC_NAME,
                structlog.processors.CallsiteParameter.THREAD,
                structlog.processors.CallsiteParameter.THREAD_NAME,
                structlog.processors.CallsiteParameter.PROCESS,
                structlog.processors.CallsiteParameter.PROCESS_NAME,
            },
        ),
        structlog.stdlib.ExtraAdder(),
    ]

    if not console_render:
        shared_processors.append(structlog.processors.dict_tracebacks)

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    configure_default_logging(
        shared_processors=shared_processors,
        logs_render=get_logs_renderer(console_render=console_render),
        log_level=log_level,
    )


def configure_default_logging(
    shared_processors: Iterable[structlog.typing.Processor] | None,
    logs_render: structlog.dev.ConsoleRenderer | structlog.processors.JSONRenderer,
    log_level: int,
) -> None:
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            logs_render,
        ],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    logging.getLogger("aiokafka").setLevel(logging.ERROR)
    logging.getLogger("asyncio_redis").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    logging.getLogger("botocore").setLevel(logging.ERROR)
    logging.getLogger("httpcore").setLevel(logging.ERROR)


def get_logs_renderer(console_render: bool) -> Callable[..., str] | JSONRenderer:
    if console_render:

        def exception_fixer(logger: structlog.dev.WrappedLogger, name: str, event_dict: structlog.dev.EventDict) -> str:
            if isinstance(event_dict.get("exception"), list):
                event_dict["exception"] = "".join(event_dict["exception"])
            return structlog.dev.ConsoleRenderer(colors=True)(logger, name, event_dict)

        return exception_fixer

    return structlog.processors.JSONRenderer(serializer=partial(ujson.dumps, ensure_ascii=False))
