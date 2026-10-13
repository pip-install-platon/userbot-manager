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
    VIDEO_NOTE = "video_note"


class MtprotoTransport(StrEnum):
    DIRECT = "direct"
    WEBSOCKET = "websocket"


class MeetingStatus(StrEnum):
    DISCUSSION = "discussion"
    TENTATIVE = "tentative"
    ADDRESS_SENT = "address_sent"
    PHONE_SHARED = "phone_shared"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


OPEN_MEETING_STATUSES: frozenset[MeetingStatus] = frozenset(
    {
        MeetingStatus.DISCUSSION,
        MeetingStatus.TENTATIVE,
        MeetingStatus.ADDRESS_SENT,
        MeetingStatus.PHONE_SHARED,
        MeetingStatus.CONFIRMED,
    }
)


CHANNEL_INCOMING_CLIENT_MESSAGE = "events.incoming_client_message"
CHANNEL_OUTGOING_TO_CLIENT = "events.outgoing_to_client"
CHANNEL_NEW_CLIENT_ASSIGNED = "events.new_client_assigned"
CHANNEL_OPERATOR_STATUS_CHANGED = "events.operator_status_changed"
CHANNEL_PROFILE_UPDATED = "events.profile_updated"
CHANNEL_AI_REPLY = "events.ai_reply"
CHANNEL_MEETING_OFFER = "events.meeting_offer"
CHANNEL_OPERATOR_ALERT = "events.operator_alert"

DEFAULT_STATUS_TTL_SECONDS = 60
DEFAULT_HEARTBEAT_TTL_SECONDS = 90
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30
PENDING_ASSIGNMENT_TTL_SECONDS = 600
TRANSIENT_MEDIA_TTL_SECONDS = 3600
PROFILE_MEDIA_LIMIT = 10
DIALOG_PAGE_SIZE = 8
DIALOG_HISTORY_LIMIT = 30
AI_HISTORY_MESSAGES = 20
MAX_TEXT_LENGTH = 4000
