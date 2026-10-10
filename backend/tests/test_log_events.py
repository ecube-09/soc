"""Log-to-timeline extraction proposes typed events; it never invents them."""

from datetime import date

from app.analysis.log_events import extract
from app.domain import EventType

SERVICES = ("payment-api", "redis-cache", "postgres-primary", "ledger-service")


def kinds(extraction):
    return [(e.type, e.service) for e in extraction.events]


def test_walkthrough_log_yields_the_onset_of_each_event_type():
    log = "\n".join(
        [
            "14:13:58 WARN  redis.pool  checkout wait 2010ms pool=50/50 waiters=57",
            "14:14:02 ERROR redis.pool  TimeoutError: timeout waiting for connection from pool",
            "14:14:02 ERROR http       POST /v1/authorisations -> 503 (upstream_unavailable)",
            "14:14:20 INFO  redis.stats server_ok=true connected_clients=418 maxclients=1500",
        ]
    )

    result = extract(log, service="payment-api", known_services=SERVICES, on_date=date(2026, 9, 27))

    assert kinds(result) == [
        (EventType.LATENCY_SPIKE, "payment-api"),
        (EventType.DEPENDENCY_TIMEOUT, "redis-cache"),
        (EventType.ERROR_SPIKE, "payment-api"),
    ]
    assert result.events[1].at.isoformat() == "2026-09-27T14:14:02"
    assert result.lines_unclassified == 1


def test_iso_timestamps_carry_their_own_date():
    result = extract(
        "2026-06-18T09:31:12 ERROR db connection to pg-node-1:5432 failed: no route to host",
        service="auth-api",
        known_services=SERVICES,
    )

    assert kinds(result) == [(EventType.DEPENDENCY_TIMEOUT, "postgres-primary")]
    assert result.events[0].at.date() == date(2026, 6, 18)


def test_a_rollback_is_a_mitigation_even_when_it_mentions_errors():
    result = extract(
        "14:40 rolled back payment-api to v2.8.0 after 503 errors",
        service="payment-api",
        on_date=date(2026, 9, 27),
    )

    assert kinds(result) == [(EventType.MITIGATION, "payment-api")]
    assert result.events[0].rule == "mitigation keyword"


def test_repeats_within_two_minutes_collapse_to_the_onset():
    log = "\n".join(
        [
            "10:00:01 ERROR http 503",
            "10:00:30 ERROR http 503",
            "10:01:50 ERROR http 503",
            "10:09:00 ERROR http 503",
        ]
    )

    result = extract(log, service="payment-api", on_date=date(2026, 9, 27))

    assert [e.at.strftime("%H:%M:%S") for e in result.events] == ["10:00:01", "10:09:00"]


def test_lines_without_a_time_are_counted_not_guessed():
    result = extract("timeout talking to redis\n", service="payment-api")

    assert result.events == ()
    assert result.lines_without_time == 1


def test_a_clock_only_log_crossing_midnight_moves_forward():
    log = "23:58 ERROR http 503\n00:03 alert PAY-5XX fired"

    result = extract(log, service="payment-api", on_date=date(2026, 9, 27))

    assert [e.at.isoformat() for e in result.events] == [
        "2026-09-27T23:58:00",
        "2026-09-28T00:03:00",
    ]
