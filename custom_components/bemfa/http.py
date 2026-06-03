"""Bemfa http apis.

API reference: https://cloud.bemfa.com/docs/src/api_device.html

All endpoints use JSON (Content-Type: application/json; charset=utf-8).
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
    DEL_TOPIC_URL,
    FETCH_TOPICS_URL,
    RENAME_TOPIC_URL,
    TOPIC_PREFIX,
)

_LOGGING = logging.getLogger(__name__)

# Bemfa API success codes:
# - New API (pro.bemfa.com): code=0
# - Old API (api.bemfa.com): code=111, status="get ok"/"add ok"/"update ok"/"del ok"
_API_SUCCESS_CODES = {0, 111}
_API_SUCCESS_STATUSES = {"get ok", "add ok", "update ok", "del ok"}


def _is_api_success(res_dict: dict) -> bool:
    """Check if Bemfa API response indicates success."""
    code = res_dict.get("code")
    status = res_dict.get("status", "")
    return code in _API_SUCCESS_CODES or status in _API_SUCCESS_STATUSES


class BemfaHttp:
    """Send http requests to bemfa service."""

    def __init__(self, hass: HomeAssistant, uid: str) -> None:
        """Initialize."""
        self._hass = hass
        self._uid = uid

    async def async_fetch_all_topics(self) -> dict[str, str]:
        """Fetch all topics created by us from bemfa service.

        Uses the legacy fetch API since the new API docs don't include
        a direct topic-listing endpoint. The legacy API returns:
        {"code": 111, "status": "get ok", "data": [{"topic_id": "...", "v_name": "..."}, ...]}
        """
        session = async_get_clientsession(self._hass)
        async with session.get(
            FETCH_TOPICS_URL.format(uid=self._uid),
        ) as res:
            try:
                res_dict = await res.json(content_type="text/html", encoding="utf-8")
            except Exception:
                _LOGGING.error("Fetch topics: failed to parse response")
                return {}
            if not _is_api_success(res_dict):
                _LOGGING.error(
                    "Fetch topics: code=%s status=%s",
                    res_dict.get("code"), res_dict.get("status", ""),
                )
                return {}
            return {
                topic["topic_id"]: topic["v_name"]
                for topic in res_dict.get("data", [])
                if topic["topic_id"].startswith(TOPIC_PREFIX)
            }

    async def async_create_topic(self, topic: str, name: str) -> None:
        """Create a topic on Bemfa cloud.

        Official API: POST https://pro.bemfa.com/v1/createTopic
        Content-Type: application/json
        Body: {"uid": "...", "topic": "led002", "type": 1, "name": "客厅灯"}
        Topic: only letters and digits (max length 64)
        Type: 1=MQTT

        Response codes:
          0     = success
          10002 = invalid parameters
          40000 = unknown error
          40006 = device already exists
          40009 = topic format error (invalid chars or too long)
        """
        if not topic.startswith(TOPIC_PREFIX):
            _LOGGING.error(
                "Reject topic '%s': must start with '%s'", topic, TOPIC_PREFIX
            )
            return
        session = async_get_clientsession(self._hass)
        _LOGGING.info("Creating Bemfa topic: '%s' name='%s'", topic, name)

        payload = {
            "uid": self._uid,
            "topic": topic,
            "type": 1,  # MQTT protocol
        }
        # name is optional per API docs, but include it if provided
        if name:
            payload["name"] = name

        _LOGGING.debug(
            "Create topic request: URL=%s payload=%s",
            CREATE_TOPIC_URL, json.dumps(payload, ensure_ascii=False),
        )

        async with session.post(
            CREATE_TOPIC_URL,
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
                _LOGGING.info("Created Bemfa topic '%s' successfully", topic)
                return

            code = res_dict.get("code")
            message = res_dict.get("message", res_dict.get("msg", ""))

            # 40006 = already exists, treat as success
            if code == 40006:
                _LOGGING.info("Topic '%s' already exists on Bemfa", topic)
                return

            _LOGGING.error(
                "Create topic '%s' failed: code=%s message=%s (full: %s)",
                topic, code, message,
                json.dumps(res_dict, ensure_ascii=False)[:300],
            )

    async def async_rename_topic(self, topic: str, name: str) -> None:
        """Rename a topic in bemfa service.

        Uses the legacy rename API since the new API docs don't include
        a dedicated rename endpoint.
        """
        if not topic.startswith(TOPIC_PREFIX):
            return
        session = async_get_clientsession(self._hass)
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
                return
            if not _is_api_success(res_dict):
                _LOGGING.warning(
                    "Rename topic '%s': code=%s status=%s",
                    topic, res_dict.get("code"), res_dict.get("status", ""),
                )

    async def async_del_topic(self, topic: str) -> None:
        """Delete a topic from Bemfa cloud.

        Official API: POST https://pro.bemfa.com/v1/deleteTopic
        Content-Type: application/json; charset=utf-8
        Body: {"uid": "...", "topic": "tttt006", "type": 1}

        Response codes:
          0     = success
          10002 = invalid parameters
          40000 = unknown error
          40004 = uid or topic error
        """
        if not topic.startswith(TOPIC_PREFIX):
            return
        session = async_get_clientsession(self._hass)
        async with session.post(
            DEL_TOPIC_URL,
            json={
                "uid": self._uid,
                "topic": topic,
                "type": 1,  # MQTT protocol
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
