"""Constants for the BTicino integration."""

from homeassistant.const import Platform

DOMAIN = "bticino_intercom"

PLATFORMS: list[Platform] = [Platform.LOCK, Platform.BINARY_SENSOR, Platform.SENSOR]

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

# Push types from websocket
PUSH_TYPE_RTC = "rtc"  # Base type for RTC events
PUSH_TYPE_WEBSOCKET_CONNECTION = f"{BRIDGE_TYPES[0]}-websocket_connection"  # Used for RTC events

# Dispatcher signal
SIGNAL_CALL_RECEIVED = f"{DOMAIN}_call_received"

# Logbook Event Types
EVENT_LOGBOOK_INCOMING_CALL = f"{DOMAIN}_incoming_call"
EVENT_LOGBOOK_ANSWERED_ELSEWHERE = f"{DOMAIN}_answered_elsewhere"
EVENT_LOGBOOK_TERMINATED = f"{DOMAIN}_terminated"

# Data keys for coordinator
DATA_LAST_EVENT = "last_event"

LOCK_TYPES = [
    "BNDL",
    "BNSL"
]

DOOR_BELL_TYPES = [
    "BNEU",
]

BRIDGE_TYPES = [
    "BNC1",
    # Aggiungi qui altri tipi di bridge supportati
]
