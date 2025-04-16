"""Constants for the BTicino integration."""

from homeassistant.const import Platform

DOMAIN = "bticino_intercom"

PLATFORMS: list[Platform] = [Platform.LOCK, Platform.BINARY_SENSOR]

# Configuration constants
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"

# Default values
DEFAULT_NAME = "BTicino Classe 100X/300X"

# Data keys (Coordinator is stored in entry.runtime_data now)
# COORDINATOR = "coordinator"
# AUTH_HANDLER = "auth_handler" # Managed by coordinator
# ACCOUNT = "account" # Managed by coordinator
# WEBSOCKET = "websocket" # Managed by coordinator

# Attributes (Access via coordinator.data or entity state)
# ATTR_MODULE_ID = "module_id"
# ATTR_HOME_ID = "home_id"

# Events / Push Types
EVENT_TYPE_INCOMING_CALL = "incoming_call"
EVENT_TYPE_ACCEPTED_CALL = "accepted_call"  # Keep if used elsewhere
EVENT_TYPE_MISSED_CALL = "missed_call"  # Keep if used elsewhere
EVENT_TYPE_WEBSOCKET_CONNECTION = "websocket_connection"  # Keep if used elsewhere

# Push types from websocket
PUSH_TYPE_WEBSOCKET_CONNECTION = "BNC1-websocket_connection"  # Keep if used elsewhere

# Dispatcher signal
SIGNAL_CALL_RECEIVED = f"{DOMAIN}_call_received"
