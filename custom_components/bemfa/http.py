"""Bemfa http apis.

API reference: https://cloud.bemfa.com/docs/src/api_device.html

Strategy: always try legacy API first (proven reliable), fall back to
new documented API if legacy fails.  All legacy APIs use form-data;
all new APIs use JSON.

Bemfa API domains:
  api.bemfa.com  — legacy (form-data) — proven reliable, used first
  apis.bemfa.com — query/modify (new, JSON) — documented fallback
  pro.bemfa.com  — create/delete (new, JSON) — documented fallback

Topic names: only letters and digits allowed (no underscores, hyphens, etc.).
Device type: identified by the last 3 digits of the topic name.
Protocol type: 1 = MQTT, 3 = TCP.
"""
from __future__ import annotations

import json
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CREATE_TOPIC_URL,
    CREATE_TOPIC_URL_NEW,
    DEL_TOPIC_URL,
    DEL_TOPIC_URL_NEW,
    FETCH_TOPICS_URL,
    FETCH_TOPICS_URL_NEW,
    RENAME_TOPIC_URL,
    RENAME_TOPIC_URL_NEW,
    TOPIC_PREFIX,
)

_LOGGING = logging.getLogger(__name__)

# Bemfa API success codes:
# - New API (pro.bemfa.com / apis.bemfa.com): code=0
# - Old API (api.bemfa.com): code=111, status="get ok"/"add ok"/"update ok"/"del ok"
# - Legacy API also returns newer success codes:
#   5723007 = "added successfullye" (note: typo in Bemfa's response)
#   5733007 = "delete successfullye"
#   5743007 = "update successfullye" (rename)
_API_SUCCESS_CODES = {0, 111, 5723007, 5733007, 5743007}
_API_SUCCESS_STATUSES = {
    "get ok", "add ok", "update ok", "del ok",
    "added successfullye", "delete successfullye", "update successfullye",
}


def _is_api_success(res_dict: dict) -> bool:
    """Check if Bemfa API response indicates success.

    Bemfa's legacy API uses inconsistent success indicators:
    - Old responses: code=111, status="add ok"/"del ok"
    - Newer responses: code=5723007/5733007/5743007,
      status="added successfullye"/"delete successfullye"/"update successfullye"
    - New JSON API: code=0

    We check both code and status to catch all known success patterns.
    """
    code = res_dict.get("code")
    status = res_dict.get("status", "")
    return code in _API_SUCCESS_CODES or status in _API_SUCCESS_STATUSES


class BemfaHttp:
    """Send http requests to bemfa service."""

    def __init__(self, hass: HomeAssistant, uid: str) -> None:
        """Initialize."""
        self._hass = hass
        self._uid = uid

    # ------------------------------------------------------------------
    #  Fetch all topics
    # ------------------------------------------------------------------

    async def async_fetch_all_topics(self) -> dict[str, str]:
        """Fetch all topics created by us from bemfa service.

        Legacy API: GET https://api.bemfa.com/api/device/v1/topic/?uid={uid}&type=2
        Response: {"code":111, "status":"get ok", "data":[{"topic_id":"...", "v_name":"..."}]}

        New API (fallback): GET https://apis.bemfa.com/vb/api/v2/allTopic?openID={uid}&type=1
        Response: {"code":0, "data":[{"topic":"cat002", "name":"home light", ...}]}
        """
        session = async_get_clientsession(self._hass)

        # --- Try legacy API first (proven reliable) ---
        try:
            async with session.get(
                FETCH_TOPICS_URL.format(uid=self._uid),
            ) as res:
                res_dict = await res.json(content_type="text/html", encoding="utf-8")
                if _is_api_success(res_dict):
                    return {
                        topic["topic_id"]: topic["v_name"]
                        for topic in res_dict.get("data", [])
                        if topic["topic_id"].startswith(TOPIC_PREFIX)
                    }
                _LOGGING.warning(
                    "Legacy fetch topics: code=%s status=%s, trying new API",
                    res_dict.get("code"), res_dict.get("status", ""),
                )
        except Exception as err:
            _LOGGING.warning("Legacy fetch topics failed: %s, trying new API", err)

        # --- Fallback to new documented API ---
        _LOGGING.info("Falling back to new fetch API")
        try:
            async with session.get(
                FETCH_TOPICS_URL_NEW.format(uid=self._uid),
            ) as res:
                res_dict = await res.json(content_type=None, encoding="utf-8")
                if _is_api_success(res_dict) and "data" in res_dict:
                    data = res_dict["data"]
                    if isinstance(data, list):
                        return {
                            item["topic"]: item.get("name", "")
                            for item in data
                            if isinstance(item, dict)
                            and item.get("topic", "").startswith(TOPIC_PREFIX)
                        }
                _LOGGING.error(
                    "New fetch API also failed: code=%s message=%s",
                    res_dict.get("code"),
                    res_dict.get("message", res_dict.get("msg", "")),
                )
        except Exception as err:
            _LOGGING.error("New fetch API also failed: %s", err)

        return {}

    # ------------------------------------------------------------------
    #  Create topic
    # ------------------------------------------------------------------

    async def async_create_topic(self, topic: str, name: str) -> None:
        """Create a topic on Bemfa cloud.

        Legacy API (first): POST https://api.bemfa.com/api/user/addtopic/
        Content-Type: application/x-www-form-urlencoded
        Body: uid=...&topic=...&type=1&name=...

        New API (fallback): POST https://pro.bemfa.com/v1/createTopic
        Content-Type: application/json
        Body: {"uid":"...", "topic":"led002", "type":1, "name":"客厅灯"}
        """
        if not topic.startswith(TOPIC_PREFIX):
            _LOGGING.error(
                "Reject topic '%s': must start with '%s'", topic, TOPIC_PREFIX
            )
            return
        session = async_get_clientsession(self._hass)
        _LOGGING.info("Creating Bemfa topic: '%s' name='%s'", topic, name)

        # --- Try legacy API first (proven reliable) ---
        try:
            async with session.post(
                CREATE_TOPIC_URL,
                data={
                    "uid": self._uid,
                    "topic": topic,
                    "type": 1,
                    "name": name,
                },
            ) as res:
                try:
                    res_dict = await res.json(content_type=None)
                except Exception:
                    res_text = await res.text()
                    _LOGGING.warning(
                        "Legacy create topic '%s': HTTP %d, body: %s",
                        topic, res.status, res_text[:300],
                    )
                else:
                    if _is_api_success(res_dict):
                        _LOGGING.info(
                            "Created Bemfa topic '%s' via legacy API", topic
                        )
                        return
                    code = res_dict.get("code")
                    # 5723006 / 40006 = topic already exists
                    if code in (5723006, 40006):
                        _LOGGING.info("Topic '%s' already exists (legacy)", topic)
                        return
                    _LOGGING.warning(
                        "Legacy create topic '%s' failed: code=%s status=%s, trying new API",
                        topic, code, res_dict.get("status", ""),
                    )
        except Exception as err:
            _LOGGING.warning(
                "Legacy create topic '%s' exception: %s, trying new API", topic, err
            )

        # --- Fallback to new documented API (JSON) ---
        payload = {
            "uid": self._uid,
            "topic": topic,
            "type": 1,
        }
        if name:
            payload["name"] = name

        _LOGGING.debug(
            "Create topic request (new API): URL=%s payload=%s",
            CREATE_TOPIC_URL_NEW, json.dumps(payload, ensure_ascii=False),
        )

        async with session.post(
            CREATE_TOPIC_URL_NEW,
            json=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
        ) as res:
            try:
                res_dict = await res.json(content_type=None)
            except Exception:
                res_text = await res.text()
                _LOGGING.error(
                    "Create topic '%s': HTTP %d, body: %s",
                    topic, res.status, res_text[:300],
                )
                return

            if _is_api_success(res_dict):
                _LOGGING.info("Created Bemfa topic '%s' via new API", topic)
                return

            code = res_dict.get("code")
            message = res_dict.get("message", res_dict.get("msg", ""))

            if code == 40006:
                _LOGGING.info("Topic '%s' already exists (new API)", topic)
                return

            _LOGGING.error(
                "Create topic '%s' failed: code=%s message=%s (full: %s)",
                topic, code, message,
                json.dumps(res_dict, ensure_ascii=False)[:300],
            )

    # ------------------------------------------------------------------
    #  Rename topic
    # ------------------------------------------------------------------

    async def async_rename_topic(self, topic: str, name: str) -> None:
        """Rename a topic in bemfa service.

        Legacy API (first): POST https://api.bemfa.com/api/device/v1/topic/name/
        Content-Type: application/x-www-form-urlencoded
        Body: uid=...&topic=...&type=1&name=...

        New API (fallback): POST https://apis.bemfa.com/va/modifyName
        Content-Type: application/json
        Body: {"uid":"...", "topic":"sn001", "type":3, "name":"卧室灯"}
        """
        if not topic.startswith(TOPIC_PREFIX):
            return
        session = async_get_clientsession(self._hass)

        # --- Try legacy API first (proven reliable) ---
        try:
            async with session.post(
                RENAME_TOPIC_URL,
                data={
                    "uid": self._uid,
                    "topic": topic,
                    "type": 1,
                    "name": name,
                },
            ) as res:
                try:
                    res_dict = await res.json(content_type=None)
                except Exception:
                    _LOGGING.warning(
                        "Legacy rename topic '%s': failed to parse response", topic
                    )
                else:
                    if _is_api_success(res_dict):
                        _LOGGING.info("Renamed topic '%s' via legacy API", topic)
                        return
                    _LOGGING.warning(
                        "Legacy rename topic '%s' failed: code=%s status=%s, trying new API",
                        topic, res_dict.get("code"), res_dict.get("status", ""),
                    )
        except Exception as err:
            _LOGGING.warning(
                "Legacy rename topic '%s' exception: %s, trying new API", topic, err
            )

        # --- Fallback to new documented API (JSON) ---
        async with session.post(
            RENAME_TOPIC_URL_NEW,
            json={
                "uid": self._uid,
                "topic": topic,
                "type": 1,
                "name": name,
            },
            headers={"Content-Type": "application/json; charset=utf-8"},
        ) as res:
            try:
                res_dict = await res.json(content_type=None)
            except Exception:
                _LOGGING.warning("New rename topic '%s': failed to parse response", topic)
                return
            if not _is_api_success(res_dict):
                _LOGGING.warning(
                    "New rename topic '%s': code=%s message=%s",
                    topic, res_dict.get("code"),
                    res_dict.get("message", res_dict.get("msg", "")),
                )

    # ------------------------------------------------------------------
    #  Delete topic
    # ------------------------------------------------------------------

    async def async_del_topic(self, topic: str) -> None:
        """Delete a topic from Bemfa cloud.

        Legacy API (first): POST https://api.bemfa.com/api/user/deltopic/
        Content-Type: application/x-www-form-urlencoded
        Body: uid=...&topic=...&type=1

        New API (fallback): POST https://pro.bemfa.com/v1/deleteTopic
        Content-Type: application/json
        Body: {"uid":"...", "topic":"tttt006", "type":1}
        """
        if not topic.startswith(TOPIC_PREFIX):
            return
        session = async_get_clientsession(self._hass)

        # --- Try legacy API first (proven reliable) ---
        try:
            async with session.post(
                DEL_TOPIC_URL,
                data={
                    "uid": self._uid,
                    "topic": topic,
                    "type": 1,
                },
            ) as res:
                try:
                    res_dict = await res.json(content_type=None)
                except Exception:
                    res_text = await res.text()
                    _LOGGING.warning(
                        "Legacy delete topic '%s': HTTP %d, body: %s",
                        topic, res.status, res_text[:300],
                    )
                else:
                    if _is_api_success(res_dict):
                        _LOGGING.info(
                            "Deleted Bemfa topic '%s' via legacy API", topic
                        )
                        return
                    _LOGGING.warning(
                        "Legacy delete topic '%s' failed: code=%s status=%s, trying new API",
                        topic, res_dict.get("code"), res_dict.get("status", ""),
                    )
        except Exception as err:
            _LOGGING.warning(
                "Legacy delete topic '%s' exception: %s, trying new API", topic, err
            )

        # --- Fallback to new documented API (JSON) ---
        async with session.post(
            DEL_TOPIC_URL_NEW,
            json={
                "uid": self._uid,
                "topic": topic,
                "type": 1,
            },
            headers={"Content-Type": "application/json; charset=utf-8"},
        ) as res:
            try:
                res_dict = await res.json(content_type=None)
            except Exception:
                res_text = await res.text()
                _LOGGING.error(
                    "Delete topic '%s': HTTP %d, body: %s",
                    topic, res.status, res_text[:300],
                )
                return
            if not _is_api_success(res_dict):
                code = res_dict.get("code")
                message = res_dict.get("message", res_dict.get("msg", ""))
                _LOGGING.error(
                    "Delete topic '%s' failed: code=%s message=%s",
                    topic, code, message,
                )
