"""Bemfa http apis."""
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


def _check_api_response(label: str, res, res_dict: dict | None) -> bool:
    """Check Bemfa API response and log errors. Returns True on success."""
    if res.status != 200:
        _LOGGING.error("%s: HTTP %d", label, res.status)
        return False
    if res_dict is None:
        _LOGGING.error("%s: empty response body", label)
        return False
    code = res_dict.get("code")
    # New API returns code=0 on success, old API returns code=111
    # Old API uses status field: "get ok", "add ok", "update ok", "del ok"
    status = res_dict.get("status", "")
    if code in (0, 111) or status in ("get ok", "add ok", "update ok", "del ok"):
        return True
    _LOGGING.error(
        "%s: API error code=%s status=%s msg=%s (full: %s)",
        label, code, status,
        res_dict.get("msg", res_dict.get("message", "")),
        json.dumps(res_dict, ensure_ascii=False)[:300],
    )
    return False


class BemfaHttp:
    """Send http requests to bemfa service."""

    def __init__(self, hass: HomeAssistant, uid: str) -> None:
        """Initialize."""
        self._hass = hass
        self._uid = uid

    async def async_fetch_all_topics(self) -> dict[str, str]:
        """Fetch all topics created by us from bemfa service."""
        session = async_get_clientsession(self._hass)
        async with session.get(
            FETCH_TOPICS_URL.format(uid=self._uid),
        ) as res:
            res_dict = await res.json(content_type="text/html", encoding="utf-8")
            if not _check_api_response("Fetch topics", res, res_dict):
                return {}
            return {
                topic["topic_id"]: topic["v_name"]
                for topic in res_dict.get("data", [])
                if topic["topic_id"].startswith(TOPIC_PREFIX)
            }

    async def async_create_topic(self, topic: str, name: str) -> None:
        """Create a topic to bemfa service using the new JSON API.

        New API: POST https://pro.bemfa.com/v1/createTopic
        Content-Type: application/json
        Body: {"uid": "...", "topic": "...", "type": 1, "name": "..."}
        Topic name: only letters and digits allowed (no underscores!)
        """
        if not topic.startswith(TOPIC_PREFIX):
            _LOGGING.error(
                "Reject topic '%s': must start with '%s'", topic, TOPIC_PREFIX
            )
            return
        session = async_get_clientsession(self._hass)
        _LOGGING.info("Creating Bemfa topic: '%s' name='%s'", topic, name)
        async with session.post(
            CREATE_TOPIC_URL,
            json={
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
                _LOGGING.error(
                    "Create topic '%s': HTTP %d, response: %s",
                    topic, res.status, res_text[:200],
                )
                return
            _check_api_response(f"Create topic '{topic}'", res, res_dict)

    async def async_rename_topic(self, topic: str, name: str) -> None:
        """Rename a topic in bemfa service."""
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
            _check_api_response(f"Rename topic '{topic}'", res, res_dict)

    async def async_del_topic(self, topic: str) -> None:
        """Delete a topic from bemfa service using the new JSON API.

        New API: POST https://pro.bemfa.com/v1/deleteTopic
        Content-Type: application/json
        Body: {"uid": "...", "topic": "...", "type": 1}
        """
        if not topic.startswith(TOPIC_PREFIX):
            return
        session = async_get_clientsession(self._hass)
        async with session.post(
            DEL_TOPIC_URL,
            json={
                "uid": self._uid,
                "topic": topic,
                "type": 1,
            },
        ) as res:
            try:
                res_dict = await res.json(content_type=None)
            except Exception:
                return
            _check_api_response(f"Delete topic '{topic}'", res, res_dict)
