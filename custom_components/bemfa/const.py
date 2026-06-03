"""Constants for the bemfa integration."""

from typing import Final

from enum import StrEnum

DOMAIN: Final = "bemfa"

# #### Config ####
CONF_UID: Final = "uid"

OPTIONS_CONFIG: Final = "config"
OPTIONS_SELECT: Final = "select"

OPTIONS_NAME: Final = "name"
OPTIONS_TYPE: Final = "type"
TYPE_OVERRIDES_KEY: Final = "type_overrides"

OPTIONS_TEMPERATURE: Final = "temperature"
OPTIONS_HUMIDITY: Final = "humidity"
OPTIONS_ILLUMINANCE: Final = "illuminance"
OPTIONS_PM25: Final = "pm25"
OPTIONS_CO2: Final = "co2"

OPTIONS_FAN_SPEED_0_VALUE: Final = "fan_speed_0_value"
OPTIONS_FAN_SPEED_1_VALUE: Final = "fan_speed_1_value"
OPTIONS_FAN_SPEED_2_VALUE: Final = "fan_speed_2_value"
OPTIONS_FAN_SPEED_3_VALUE: Final = "fan_speed_3_value"
OPTIONS_FAN_SPEED_4_VALUE: Final = "fan_speed_4_value"
OPTIONS_FAN_SPEED_5_VALUE: Final = "fan_speed_5_value"

OPTIONS_SWING_OFF_VALUE: Final = "swing_off_value"
OPTIONS_SWING_HORIZONTAL_VALUE: Final = "swing_horizontal_value"
OPTIONS_SWING_VERTICAL_VALUE: Final = "swing_vertical_value"
OPTIONS_SWING_BOTH_VALUE: Final = "swing_both_value"

# #### MQTT ####
class TopicSuffix(StrEnum):
    """Suffix for bemfa MQTT topic"""

    PLUG = "001"
    LIGHT = "002"
    FAN = "003"
    SENSOR = "004"
    CLIMATE = "005"
    SWITCH = "006"
    COVER = "009"


# Mapping from Bemfa topic suffix to display name (Chinese + English)
BEMFA_TYPE_MAP: dict[str, str] = {
    TopicSuffix.PLUG: "插座 (Plug)",
    TopicSuffix.LIGHT: "灯 (Light)",
    TopicSuffix.FAN: "风扇 (Fan)",
    TopicSuffix.SENSOR: "传感器 (Sensor)",
    TopicSuffix.CLIMATE: "空调 (Climate)",
    TopicSuffix.SWITCH: "开关 (Switch)",
    TopicSuffix.COVER: "窗帘 (Cover)",
}

# Mapping from HA entity domain to default Bemfa type suffix.
# Used to determine the default type when adding entities.
# Domains not listed here default to SWITCH (006).
DOMAIN_TYPE_MAP: dict[str, str] = {
    "light": TopicSuffix.LIGHT,
    "fan": TopicSuffix.FAN,
    "sensor": TopicSuffix.SENSOR,
    "binary_sensor": TopicSuffix.SENSOR,
    "climate": TopicSuffix.CLIMATE,
    "cover": TopicSuffix.COVER,
    # All switch-like domains default to SWITCH
    "switch": TopicSuffix.SWITCH,
    "script": TopicSuffix.SWITCH,
    "input_boolean": TopicSuffix.SWITCH,
    "input_button": TopicSuffix.SWITCH,
    "automation": TopicSuffix.SWITCH,
    "humidifier": TopicSuffix.SWITCH,
    "remote": TopicSuffix.SWITCH,
    "siren": TopicSuffix.SWITCH,
    "camera": TopicSuffix.SWITCH,
    "media_player": TopicSuffix.SWITCH,
    "lock": TopicSuffix.SWITCH,
    "scene": TopicSuffix.SWITCH,
    "group": TopicSuffix.SWITCH,
    "vacuum": TopicSuffix.SWITCH,
}


MQTT_HOST: Final = "bemfa.com"
MQTT_PORT: Final = 9501
MQTT_KEEPALIVE: Final = 600
TOPIC_PUBLISH: Final = "{topic}/set"
TOPIC_PREFIX: Final = "hass"
TOPIC_PING: Final = f"{TOPIC_PREFIX}ping"
INTERVAL_PING_SEND = 30  # send ping msg every 30s
INTERVAL_PING_RECEIVE = 20  # detect a ping lost in 20s after a ping message send
MAX_PING_LOST = 3  # reconnect to mqtt server when 3 continous ping losts detected
MSG_SEPARATOR: Final = "#"
MSG_ON: Final = "on"
MSG_OFF: Final = "off"
MSG_PAUSE: Final = "pause"  # for covers
MSG_SPEED_COUNT: Final = 4  # for fans, 4 speed supported at most

# #### Service Api ####
# Reference: https://cloud.bemfa.com/docs/src/api_device.html
#
# Strategy: always try legacy API first (proven reliable), fall back to
# new documented API if legacy fails.  All legacy APIs use form-data;
# all new APIs use JSON.
#
# Bemfa API domains:
#   api.bemfa.com  — legacy (form-data) — proven reliable, used first
#   apis.bemfa.com — query/modify (new, JSON) — documented fallback
#   pro.bemfa.com  — create/delete (new, JSON) — documented fallback

# --- Fetch all topics ---
# Legacy: GET https://api.bemfa.com/api/device/v1/topic/?uid={uid}&type=2
FETCH_TOPICS_URL: Final = "https://api.bemfa.com/api/device/v1/topic/?uid={uid}&type=2"
# New documented API (fallback): GET .../allTopic?openID={uid}&type=1
FETCH_TOPICS_URL_NEW: Final = "https://apis.bemfa.com/vb/api/v2/allTopic?openID={uid}&type=1"

# --- Create topic ---
# Legacy: POST https://api.bemfa.com/api/user/addtopic/  (form-data)
CREATE_TOPIC_URL: Final = f"https://api.{MQTT_HOST}/api/user/addtopic/"
# New documented API (fallback): POST .../createTopic  (JSON)
CREATE_TOPIC_URL_NEW: Final = "https://pro.bemfa.com/v1/createTopic"

# --- Delete topic ---
# Legacy: POST https://api.bemfa.com/api/user/deltopic/  (form-data)
DEL_TOPIC_URL: Final = f"https://api.{MQTT_HOST}/api/user/deltopic/"
# New documented API (fallback): POST .../deleteTopic  (JSON)
DEL_TOPIC_URL_NEW: Final = "https://pro.bemfa.com/v1/deleteTopic"

# --- Rename topic ---
# Legacy: POST https://api.bemfa.com/api/device/v1/topic/name/  (form-data)
RENAME_TOPIC_URL: Final = f"https://api.{MQTT_HOST}/api/device/v1/topic/name/"
# New documented API (fallback): POST .../modifyName  (JSON)
RENAME_TOPIC_URL_NEW: Final = "https://apis.bemfa.com/va/modifyName"
