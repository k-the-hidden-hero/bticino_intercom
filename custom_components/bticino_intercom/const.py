"""Constants for the BTicino integration."""

from homeassistant.const import Platform

DOMAIN = "bticino_intercom"

PLATFORMS: list[Platform] = [
    Platform.LOCK,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.LIGHT,
    Platform.CAMERA,
    Platform.EVENT,
]


# Default values
DEFAULT_NAME = "BTicino Classe 100X/300X"

# Data keys are managed internally or via coordinator

# Events / Push Types
EVENT_TYPE_INCOMING_CALL = "incoming_call"
EVENT_TYPE_ANSWERED_ELSEWHERE = "answered_elsewhere"
EVENT_TYPE_TERMINATED = "terminated"
EVENT_TYPE_MISSED_CALL = "missed_call"
EVENT_TYPE_ACCEPTED_CALL = "accepted_call"

# Module Types (Based on Variant Subtype)
SUBTYPE_DOORLOCK = "bndl_doorlock"
SUBTYPE_STAIRCASE_LIGHT = "bnsl_staircase_light"
SUBTYPE_EXTERNAL_UNIT = "bneu_external_unit"
# Add other known subtypes here if needed

# Mapping from subtype to Home Assistant platform/domain
SUBTYPE_TO_PLATFORM = {
    SUBTYPE_DOORLOCK: Platform.LOCK,
    SUBTYPE_STAIRCASE_LIGHT: Platform.LIGHT,
    SUBTYPE_EXTERNAL_UNIT: Platform.BINARY_SENSOR,  # Assuming external unit acts as a doorbell sensor
}

# The raw module "type" is the canonical, always-present discriminator. It is
# returned for both the Classe 100X/300X and the Classe 300 EOS (bridge BNCX),
# whereas the verbose "variant" field is only present on the older models. We map
# the type directly so a single code path covers every device family.
TYPE_TO_SUBTYPE = {
    "BNDL": SUBTYPE_DOORLOCK,
    "BNSL": SUBTYPE_STAIRCASE_LIGHT,
    "BNEU": SUBTYPE_EXTERNAL_UNIT,
}

# Subtypes already handled by the integration. A "variant" carrying a subtype
# outside this set is treated as a newer Netatmo variant and then takes
# precedence over the type-derived value (forward-compat override).
KNOWN_SUBTYPES = frozenset(SUBTYPE_TO_PLATFORM)


# Push types from websocket
PUSH_TYPE_RTC = "rtc"  # Base type for RTC events

# Dispatcher signal
SIGNAL_CALL_RECEIVED = f"{DOMAIN}_call_received"

# Logbook Event Types
EVENT_LOGBOOK_INCOMING_CALL = f"{DOMAIN}_incoming_call"
EVENT_LOGBOOK_ANSWERED_ELSEWHERE = f"{DOMAIN}_answered_elsewhere"
EVENT_LOGBOOK_TERMINATED = f"{DOMAIN}_terminated"
EVENT_LOGBOOK_MISSED_CALL = f"{DOMAIN}_missed_call"
EVENT_LOGBOOK_ACCEPTED_CALL = f"{DOMAIN}_accepted_call"

# Frontend / automation event
EVENT_CALL = f"{DOMAIN}_call"

# Data keys for coordinator
DATA_LAST_EVENT = "last_event"

# Coordinator update interval (in minutes)
UPDATE_INTERVAL = 5

# Light specific constants
LIGHT_AUTO_OFF_DELAY = 10  # seconds

# Lock specific constants
LOCK_RELOCK_DELAY = 5  # seconds

# Call sensor specific constants
CALL_SENSOR_TIMEOUT = 30

# Camera specific constants
IMAGE_CACHE_SECONDS = 300  # 5 minutes

# --- Call session tracking ---
# If no "call" retransmissions arrive within this many seconds, the session
# is closed (fallback for when rescind/terminate never arrives).
CALL_RETRANSMIT_WINDOW = 15
# Absolute cap on session duration, last safety net.
CALL_SESSION_MAX_DURATION = 180
# Window during which a session_id is remembered as "recently closed",
# so duplicate terminate/rescind RTC events (Netatmo push retransmits
# to multiple receivers) don't trigger redundant API refreshes. Fixes #56.
CLOSED_SESSION_DEDUP_WINDOW = 60  # seconds

# --- Event history ---
HISTORY_STORAGE_VERSION = 1
HISTORY_DEFAULT_RETENTION_DAYS = 30
HISTORY_DEFAULT_MAX_EVENTS = 500
HISTORY_MAX_RETENTION_DAYS = 365
HISTORY_MAX_MAX_EVENTS = 5000

# Option keys (kept as plain strings to avoid breaking existing entries)
OPT_HISTORY_ENABLED = "history_enabled"
OPT_HISTORY_RETENTION_DAYS = "history_retention_days"
OPT_HISTORY_MAX_EVENTS = "history_max_events"
