class IngestError(Exception):
    """Base class for ingest pipeline errors."""


class BadRequestError(IngestError):
    """Raised when inbound payload format is invalid."""


class UnauthorizedError(IngestError):
    """Raised when inbound request authentication fails."""


class ConfigurationError(IngestError):
    """Raised when service configuration is invalid."""


class PublishUnavailableError(IngestError):
    """Raised when the broker cannot accept or route a publish (timeout, blocked connection, unroutable)."""
