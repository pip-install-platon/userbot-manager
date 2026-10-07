from enum import StrEnum


class OperatorStatus(StrEnum):
    FREE = "free"
    BUSY = "busy"
    PAUSED = "paused"


class ClientState(StrEnum):
    NEW = "new"
    ROUTED = "routed"
    IN_DIALOG = "in_dialog"
    CLOSED = "closed"


class Direction(StrEnum):
    INCOMING = "in"
    OUTGOING = "out"


class MediaKind(StrEnum):
    PHOTO = "photo"
    VIDEO = "video"


CHANNEL_INCOMING_CLIENT_MESSAGE = "events.incoming_client_message"
CHANNEL_OUTGOING_TO_CLIENT = "events.outgoing_to_client"
CHANNEL_NEW_CLIENT_ASSIGNED = "events.new_client_assigned"
CHANNEL_OPERATOR_STATUS_CHANGED = "events.operator_status_changed"
CHANNEL_PROFILE_UPDATED = "events.profile_updated"

DEFAULT_STATUS_TTL_SECONDS = 60
DEFAULT_HEARTBEAT_TTL_SECONDS = 90
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30
PENDING_ASSIGNMENT_TTL_SECONDS = 600
TRANSIENT_MEDIA_TTL_SECONDS = 3600
PROFILE_MEDIA_LIMIT = 10
DIALOG_PAGE_SIZE = 8
DIALOG_HISTORY_LIMIT = 30
MAX_TEXT_LENGTH = 4000
