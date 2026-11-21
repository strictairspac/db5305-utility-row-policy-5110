from enum import StrEnum


class Role(StrEnum):
    OPERATOR = 'operator'
    REVIEWER = 'reviewer'
    VIEWER = 'viewer'


class Status(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETE = 'complete'
    FAILED = 'failed'


class CorrectionKind(StrEnum):
    # A single revocation that must take effect on its own, without waiting for the
    # re-extension that may follow in a separate message.
    URGENT_REVOKE = 'urgent_revoke'
    # A shift handover whose revocation and re-extension arrive separately and are
    # only published together once both parts have been collected.
    HANDOVER = 'handover'


class CorrectionStatus(StrEnum):
    PENDING = 'pending'
    PUBLISHED = 'published'
