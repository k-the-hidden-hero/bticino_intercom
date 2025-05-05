"""Constants for the BTicino integration."""

from homeassistant.const import Platform

DOMAIN = "bticino_intercom"

PLATFORMS: list[Platform] = [
    Platform.LOCK,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.LIGHT,
]

# Configuration constants
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"

# Default values
DEFAULT_NAME = "BTicino Classe 100X/300X"

# Data keys are managed internally or via coordinator

# Events / Push Types
EVENT_TYPE_INCOMING_CALL = "incoming_call"
EVENT_TYPE_ANSWERED_ELSEWHERE = "answered_elsewhere"
EVENT_TYPE_TERMINATED = "terminated"

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

# Deprecated Type Lists (BRIDGE_TYPES kept for websocket event check)
# BRIDGE_TYPES = [
#     "BNC1",
# ]

# LOCK_TYPES = [
#     "BNDL",
# ]
#
# LIGHT_TYPES = [
#     "BNSL",
# ]
#
# DOOR_BELL_TYPES = [
#     "BNEU",
# ]

# Combine all module types (consider removing or adjusting if only variant is used)
# MODULE_TYPES = BRIDGE_TYPES + LOCK_TYPES + LIGHT_TYPES + DOOR_BELL_TYPES

# Push types from websocket
PUSH_TYPE_RTC = "rtc"  # Base type for RTC events
# PUSH_TYPE_WEBSOCKET_CONNECTION = (
#     f"{BRIDGE_TYPES[0]}-websocket_connection"  # Might need adjustment if bridge ID logic changes how we reference it
# )

# Dispatcher signal
SIGNAL_CALL_RECEIVED = f"{DOMAIN}_call_received"

# Logbook Event Types
EVENT_LOGBOOK_INCOMING_CALL = f"{DOMAIN}_incoming_call"
EVENT_LOGBOOK_ANSWERED_ELSEWHERE = f"{DOMAIN}_answered_elsewhere"
EVENT_LOGBOOK_TERMINATED = f"{DOMAIN}_terminated"

# Data keys for coordinator
DATA_LAST_EVENT = "last_event"

# Coordinator update interval (in minutes)
UPDATE_INTERVAL = 5

# Light specific constants
LIGHT_AUTO_OFF_DELAY = 10  # seconds

# Lock specific constants
LOCK_RELOCK_DELAY = 5  # seconds
