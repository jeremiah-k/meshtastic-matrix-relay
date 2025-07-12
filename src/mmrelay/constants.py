from enum import Enum


class MatrixMsgTypes(Enum):
    EMOTE = "m.emote"
    IMAGE = "m.image"
    MESSAGE = "m.room.message"
    NOTICE = "m.notice"
    TEXT = "m.text"


class MatrixHTML(Enum):
    FORMAT = "org.matrix.custom.html"


class MeshtasticPorts(Enum):
    DETECTION_SENSOR_APP = "DETECTION_SENSOR_APP"
    TELEMETRY_APP = "TELEMETRY_APP"
    TEXT_MESSAGE_APP = "TEXT_MESSAGE_APP"


class App(Enum):
    AUTHOR = None
    NAME = "mmrelay"


class ConfigKeys(Enum):
    ACCESS_TOKEN = "access_token"
    ACTIVE = "active"
    ANONYMIZE = "anonymize"
    AT = "at"
    BLE_ADDRESS = "ble_address"
    BOT_USER_ID = "bot_user_id"
    BROADCAST_ENABLED = "broadcast_enabled"
    CHANNELS = "channels"
    COMMUNITY_PLUGINS = "community-plugins"
    CONNECTION_TYPE = "connection_type"
    CUSTOM_PLUGINS = "custom-plugins"
    DATABASE = "database"
    DB = "db"  # Deprecated in favor of DATABASE
    DETECTION_SENSOR = "detection_sensor"
    ENABLED = "enabled"
    HEALTH_CHECK = "health_check"
    HEARTBEAT_INTERVAL = "heartbeat_interval"
    HOST = "host"
    HOMESERVER = "homeserver"
    HOURS = "hours"
    IMAGE_HEIGHT = "image_height"
    IMAGE_WIDTH = "image_width"
    LEVEL = "level"
    LOGGING = "logging"
    MATRIX = "matrix"
    MATRIX_ROOMS = "matrix_rooms"
    MESSAGE_INTERACTIONS = "message_interactions"
    MESHTASTIC = "meshtastic"
    MESHNET_NAME = "meshnet_name"
    MINUTES = "minutes"
    MSGS_TO_KEEP = "msgs_to_keep"
    MSG_MAP = "msg_map"
    PLUGINS = "plugins"
    PLUGIN_RESPONSE_DELAY = "plugin_response_delay"
    RADIUS = "radius"
    RADIUS_KM = "radius_km"
    REACTIONS = "reactions"
    RELAY_REACTIONS = "relay_reactions"  # Deprecated in favor of MESSAGE_INTERACTIONS
    REPLIES = "replies"
    SCHEDULE = "schedule"
    SERIAL_PORT = "serial_port"
    UNITS = "units"
    WIPE_ON_RESTART = "wipe_on_restart"
    ZOOM = "zoom"


class DeprecatedConfigKeys(Enum):
    DB = "db"
    NETWORK = "network"
    RELAY_REACTIONS = "relay_reactions"


class ConnectionTypes(Enum):
    BLE = "ble"
    NETWORK = "network"  # Deprecated in favor of TCP
    SERIAL = "serial"
    TCP = "tcp"


class Telemetry(Enum):
    AIR_UTIL_TX = "airUtilTx"
    BATTERY_LEVEL = "batteryLevel"
    VOLTAGE = "voltage"


class Weather(Enum):
    TEMP_C = "°C"
    TEMP_F = "°F"
    UNITS_IMPERIAL = "imperial"
    UNITS_METRIC = "metric"
