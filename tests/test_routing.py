from event_receiver.config import _default_route_rules
from event_receiver.routing import EventRouter, RouteRule, RoutingConfig


def _make_router(rules: list[RouteRule], default: str = "events.unrouted") -> EventRouter:
    return EventRouter(RoutingConfig(default_destination=default, rules=rules))


def test_exact_match() -> None:
    router = _make_router(
        [
            RouteRule(destination="events.booking.lifecycle", source_pattern="booking", type_pattern="booking.created"),
        ],
    )
    assert (
        router.resolve_routing_key_by_fields(source="booking", event_type="booking.created")
        == "events.booking.lifecycle"
    )


def test_glob_source_pattern() -> None:
    router = _make_router(
        [
            RouteRule(destination="events.jitsi", source_pattern="jitsi*", type_pattern="*"),
        ],
    )
    assert router.resolve_routing_key_by_fields(source="jitsi-meet", event_type="any.event") == "events.jitsi"


def test_glob_type_pattern() -> None:
    router = _make_router(
        [
            RouteRule(destination="events.mail", source_pattern="unisender-go", type_pattern="unisender.*"),
        ],
    )
    assert (
        router.resolve_routing_key_by_fields(source="unisender-go", event_type="unisender.delivered") == "events.mail"
    )


def test_fallback_to_default() -> None:
    router = _make_router(
        [
            RouteRule(destination="events.booking.lifecycle", source_pattern="booking", type_pattern="booking.created"),
        ],
    )
    assert router.resolve_routing_key_by_fields(source="unknown", event_type="unknown.event") == "events.unrouted"


def test_first_match_wins() -> None:
    router = _make_router(
        [
            RouteRule(destination="first", source_pattern="*", type_pattern="booking.created"),
            RouteRule(destination="second", source_pattern="booking", type_pattern="booking.created"),
        ],
    )
    assert router.resolve_routing_key_by_fields(source="booking", event_type="booking.created") == "first"


def test_no_rules_uses_default() -> None:
    router = _make_router([], default="events.fallback")
    assert router.resolve_routing_key_by_fields(source="any", event_type="any") == "events.fallback"


def test_source_mismatch_skips_rule() -> None:
    router = _make_router(
        [
            RouteRule(destination="events.booking.lifecycle", source_pattern="booking", type_pattern="booking.created"),
        ],
    )
    assert router.resolve_routing_key_by_fields(source="other", event_type="booking.created") == "events.unrouted"


def test_type_mismatch_skips_rule() -> None:
    router = _make_router(
        [
            RouteRule(destination="events.booking.lifecycle", source_pattern="booking", type_pattern="booking.created"),
        ],
    )
    assert router.resolve_routing_key_by_fields(source="booking", event_type="booking.cancelled") == "events.unrouted"


def test_default_routing_rules_booking_lifecycle() -> None:
    """Verify that the actual default routing rules send booking events to the correct queue."""
    router = _make_router(_default_route_rules(), default="events.unrouted")

    assert (
        router.resolve_routing_key_by_fields(source="booking", event_type="booking.created")
        == "events.booking.lifecycle"
    )
    assert (
        router.resolve_routing_key_by_fields(source="booking", event_type="booking.cancelled")
        == "events.booking.lifecycle"
    )
    assert (
        router.resolve_routing_key_by_fields(source="booking", event_type="booking.rescheduled")
        == "events.booking.lifecycle"
    )
    assert (
        router.resolve_routing_key_by_fields(source="booking", event_type="booking.reassigned")
        == "events.booking.lifecycle"
    )
    # events.booking.reminder queue was removed: reminder_sent persists via lifecycle
    assert (
        router.resolve_routing_key_by_fields(source="booking", event_type="booking.reminder_sent")
        == "events.booking.lifecycle"
    )


def test_default_routing_rules_chat() -> None:
    router = _make_router(_default_route_rules(), default="events.unrouted")

    assert router.resolve_routing_key_by_fields(source="booking", event_type="chat.created") == "events.chat.lifecycle"
    assert router.resolve_routing_key_by_fields(source="booking", event_type="chat.deleted") == "events.chat.lifecycle"
    assert (
        router.resolve_routing_key_by_fields(source="booking", event_type="chat.message_sent") == "events.chat.activity"
    )


def test_default_routing_rules_external() -> None:
    router = _make_router(_default_route_rules(), default="events.unrouted")

    assert router.resolve_routing_key_by_fields(source="jitsi-meet", event_type="conference.joined") == "events.jitsi"
    assert (
        router.resolve_routing_key_by_fields(source="unisender-go", event_type="unisender.delivered") == "events.mail"
    )
    assert router.resolve_routing_key_by_fields(source="getstream", event_type="getstream.message.new") == "events.chat"
