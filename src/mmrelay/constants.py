from enum import Enum


class MatrixMsgTypes(Enum):
    TEXT = "m.text"
    EMOTE = "m.emote"
    IMAGE = "m.image"
    NOTICE = "m.notice"
    MESSAGE = "m.room.message"


class MatrixHTML(Enum):
    FORMAT = "org.matrix.custom.html"


class MeshtasticPorts(Enum):
    TEXT_MESSAGE_APP = "TEXT_MESSAGE_APP"
    DETECTION_SENSOR_APP = "DETECTION_SENSOR_APP"
    TELEMETRY_APP = "TELEMETRY_APP"


class App(Enum):
    NAME = "mmrelay"
    AUTHOR = None


class ConfigKeys(Enum):
    MATRIX = "matrix"
    MESHTASTIC = "meshtastic"
    MATRIX_ROOMS = "matrix_rooms"
    LOGGING = "logging"
    LEVEL = "level"
    PLUGINS = "plugins"
    COMMUNITY_PLUGINS = "community-plugins"
    CUSTOM_PLUGINS = "custom-plugins"
    ACTIVE = "active"
    CHANNELS = "channels"
    SCHEDULE = "schedule"
    AT = "at"
    HOURS = "hours"
    MINUTES = "minutes"
    PLUGIN_RESPONSE_DELAY = "plugin_response_delay"
    RADIUS_KM = "radius_km"
    ZOOM = "zoom"
    IMAGE_WIDTH = "image_width"
    IMAGE_HEIGHT = "image_height"
    ANONYMIZE = "anonymize"
    RADIUS = "radius"
    UNITS = "units"
    DATABASE = "database"
    MSG_MAP = "msg_map"
    WIPE_ON_RESTART = "wipe_on_restart"
    DB = "db"
    MSGS_TO_KEEP = "msgs_to_keep"
    HOMESERVER = "homeserver"
    ACCESS_TOKEN = "access_token"
    BOT_USER_ID = "bot_user_id"
    MESSAGE_INTERACTIONS = "message_interactions"
    REACTIONS = "reactions"
    REPLIES = "replies"
    RELAY_REACTIONS = "relay_reactions"
    BROADCAST_ENABLED = "broadcast_enabled"
    DETECTION_SENSOR = "detection_sensor"
    CONNECTION_TYPE = "connection_type"
    SERIAL_PORT = "serial_port"
    BLE_ADDRESS = "ble_address"
    HOST = "host"
    HEALTH_CHECK = "health_check"
    ENABLED = "enabled"
    HEARTBEAT_INTERVAL = "heartbeat_interval"
    MESHNET_NAME = "meshnet_name"


class ConnectionTypes(Enum):
    SERIAL = "serial"
    BLE = "ble"
    TCP = "tcp"
    NETWORK = "network"


class Telemetry(Enum):
    BATTERY_LEVEL = "batteryLevel"
    VOLTAGE = "voltage"
    AIR_UTIL_TX = "airUtilTx"


class Weather(Enum):
    UNITS_METRIC = "metric"
    UNITS_IMPERIAL = "imperial"
    TEMP_C = "°C"
    TEMP_F = "°F"
