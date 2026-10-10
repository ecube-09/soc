from enum import StrEnum


class Environment(StrEnum):
    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"


class Severity(StrEnum):
    SEV1 = "SEV-1"
    SEV2 = "SEV-2"
    SEV3 = "SEV-3"
    SEV4 = "SEV-4"


class EventType(StrEnum):
    """Closed set, so two timelines can be compared without asking the model."""

    DEPLOY = "deploy"
    ERROR_SPIKE = "error_spike"
    LATENCY_SPIKE = "latency_spike"
    DEPENDENCY_TIMEOUT = "dependency_timeout"
    ALERT = "alert"
    MITIGATION = "mitigation"


class ActionDirection(StrEnum):
    INCREASE = "increase"
    DECREASE = "decrease"
    SCALE_OUT = "scale_out"
    SCALE_IN = "scale_in"
    ROLLBACK = "rollback"
    RESTART = "restart"
    FAILOVER = "failover"
    RECONFIGURE = "reconfigure"
    PATCH = "patch"


OPPOSING_DIRECTIONS: frozenset[frozenset[ActionDirection]] = frozenset(
    {
        frozenset({ActionDirection.INCREASE, ActionDirection.DECREASE}),
        frozenset({ActionDirection.SCALE_OUT, ActionDirection.SCALE_IN}),
    }
)


def directions_conflict(left: ActionDirection, right: ActionDirection) -> bool:
    return frozenset({left, right}) in OPPOSING_DIRECTIONS


class SourceType(StrEnum):
    """What a retrievable chunk came from; also the citation namespace."""

    INCIDENT = "incident"
    POSTMORTEM = "postmortem"
    RUNBOOK = "runbook"
    LOG_EXCERPT = "log_excerpt"
    SERVICE_MAP = "service_map"


class ArtifactKind(StrEnum):
    """Everything a case can carry, including kinds the MVP deliberately rejects."""

    INCIDENT = "incident"
    LOG_EXCERPT = "log_excerpt"
    POSTMORTEM = "postmortem"
    RUNBOOK = "runbook"
    SERVICE_MAP = "service_map"

    PCAP = "pcap"
    FILE_SAMPLE = "file_sample"
    SECURITY_DOC = "security_doc"


SUPPORTED_ARTIFACT_KINDS: frozenset[ArtifactKind] = frozenset(
    {
        ArtifactKind.INCIDENT,
        ArtifactKind.LOG_EXCERPT,
        ArtifactKind.POSTMORTEM,
        ArtifactKind.RUNBOOK,
        ArtifactKind.SERVICE_MAP,
    }
)

RESERVED_ARTIFACT_KINDS: frozenset[ArtifactKind] = frozenset(
    {ArtifactKind.PCAP, ArtifactKind.FILE_SAMPLE, ArtifactKind.SECURITY_DOC}
)


def assert_artifact_kind_supported(kind: ArtifactKind | str) -> ArtifactKind:
    """Raise ValueError for reserved kinds that are not implemented yet."""
    value = ArtifactKind(kind)
    if value not in SUPPORTED_ARTIFACT_KINDS:
        raise ValueError(
            f"Artifact kind {value.value!r} is not supported in this version "
            "(reserved for a later analyzer)."
        )
    return value


class EvalRole(StrEnum):
    """Labeled roles used by the seed corpus and later eval scripts."""

    MATCHING = "matching"
    CONFLICTING = "conflicting"
    DISTRACTOR_SERVICE = "distractor_service"
    DISTRACTOR_ENV = "distractor_env"
    STALE = "stale"
    HISTORICAL = "historical"
    CURRENT_MATCHING = "current_matching"
    WEAK = "weak"


class RootCauseStatus(StrEnum):
    """There is no `confirmed`. The system proposes; a human confirms."""

    UNDETERMINED = "undetermined"
    HYPOTHESIS = "hypothesis"


class ResponseMode(StrEnum):
    INVESTIGATION = "investigation"
    ESCALATION = "escalation"
