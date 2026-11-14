from enum import StrEnum
class Role(StrEnum):
    ADMIN="admin"
    ANALYST="analyst"
    VIEWER="viewer"
class RunStatus(StrEnum):
    QUEUED="queued"
    RUNNING="running"
    COMPLETED="completed"
    FAILED="failed"
class QualificationMode(StrEnum):
    RAW="raw"
    CALIBRATION="calibration_only"
class QualificationStatus(StrEnum):
    DRAFT="draft"
    APPROVED="approved"
class VersionSource(StrEnum):
    ORIGINAL="original"
    RECALC="recalc"
class VersionStatus(StrEnum):
    PENDING="pending"
    CANDIDATE="candidate"
    ACTIVE="active"
    SUPERSEDED="superseded"
    DISCARDED="discarded"
class BatchStatus(StrEnum):
    QUEUED="queued"
    RUNNING="running"
    READY="ready"
    FAILED="failed"
    REVOKED="revoked"
    ACTIVATED="activated"
class ItemStatus(StrEnum):
    QUEUED="queued"
    RUNNING="running"
    READY="ready"
    FAILED="failed"
    REVOKED="revoked"
