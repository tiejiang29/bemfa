"""Support for bemfa service."""
from __future__ import annotations

import logging

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, Event, HomeAssistant
from .sync import SYNC_TYPES, Sync
from .const import OPTIONS_NAME, TOPIC_PING
from .http import BemfaHttp
from .mqtt import BemfaMqtt

_LOGGING = logging.getLogger(__name__)


class BemfaService:
    """Service handles mqtt topocs and connection."""

    def __init__(self, hass: HomeAssistant, uid: str) -> None:
        """Initialize."""
        self._hass = hass
        self._bemfa_http = BemfaHttp(hass, uid)
        self._bemfa_mqtt = BemfaMqtt(hass, uid, None)

    async def async_start(self, config: dict[str, dict[str, str]]) -> None:
        """Start the servcie, called when Bemfa component starts."""
        all_topics = await self._bemfa_http.async_fetch_all_topics()

        # make sure we have the ping topic for heartbeat packages
        if TOPIC_PING not in all_topics:
            await self._bemfa_http.async_create_topic(TOPIC_PING, "ping")
        else:
            # This topic does not matter to entities, remove it for following steps
            del all_topics[TOPIC_PING]

        # time to make mqtt connection (now async to avoid blocking)
        await self._bemfa_mqtt.async_connect()

        # When sync an entity to bemfa service,
        # we must make sure this entity's state is available, means this entity has inited.
        # So a check of hass state is necessary.
        def _start(event: Event | None = None):
            active_syncs = self.collect_supported_syncs()
            for sync in active_syncs:
                if sync.topic in all_topics:
                    sync.name = all_topics[sync.topic]
                    if sync.topic in config:
                        sync.config = config[sync.topic]
                    self._bemfa_mqtt.create_sync(sync)

            # Detect orphan topics: exist on Bemfa cloud but no HA entity matches
            self._detect_orphan_topics(all_topics, active_syncs, config)

        if self._hass.state == CoreState.running:
            _start()
        else:
            # for situations when hass restarts
            self._hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _start)

    async def async_fetch_all_topics(
        self,
    ) -> dict[str, str]:  # topic -> name
        """Fetch topics we created from benfa servcie, include which do not exist in hass."""
        all_topics = await self._bemfa_http.async_fetch_all_topics()

        if TOPIC_PING in all_topics:
            del all_topics[TOPIC_PING]

        return all_topics

    def collect_supported_syncs(self) -> list[Sync]:
        """Collect all supported hass-to-bemfa syncs."""
        syncs: list[Sync] = []
        for sync_type in SYNC_TYPES.values():
            syncs.extend(sync_type.collect_supported_syncs(self._hass))
        return sorted(syncs, key=lambda item: item.entity_id)

    def _detect_orphan_topics(
        self,
        all_topics: dict[str, str],
        active_syncs: list[Sync],
        config: dict[str, dict[str, str]],
    ) -> None:
        """Detect orphan topics on Bemfa cloud that have no matching HA entity.

        An orphan topic is one that:
        1. Exists on Bemfa cloud (present in all_topics)
        2. No current HA entity generates a matching topic (not in active_syncs)
        3. Not stored in user config (not intentionally kept)

        When orphans are found, a persistent notification is created in HA
        to alert the user to clean them up manually.
        """
        # Collect all topics that current HA entities would generate
        active_topics = {sync.topic for sync in active_syncs}

        # Find orphans: cloud topics with no HA entity and no user config
        orphans: dict[str, str] = {}
        for topic, name in all_topics.items():
            if topic not in active_topics and topic not in config:
                orphans[topic] = name

        if not orphans:
            # Dismiss any previous orphan notification since there are none now
            self._hass.async_create_task(
                self._hass.services.async_call(
                    "persistent_notification",
                    "dismiss",
                    {"notification_id": "bemfa_orphan_topics"},
                )
            )
            return

        _LOGGING.warning(
            "Detected %d orphan topic(s) on Bemfa cloud with no matching HA entity",
            len(orphans),
        )

        # Build notification message with orphan details
        orphan_lines = []
        for topic, name in orphans.items():
            orphan_lines.append(f"- `{topic}` ({name})")
        orphan_list = "\n".join(orphan_lines)

        # Use service call which is always available, unlike
        # hass.components.persistent_notification which may not be loaded
        self._hass.async_create_task(
            self._hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Bemfa 孤儿 Topic 检测",
                    "message": (
                        f"巴法云上有 **{len(orphans)}** 个孤儿 Topic 未关联任何 HA 实体：\n\n"
                        f"{orphan_list}\n\n"
                        f"这些 Topic 可能是实体被删除、重命名或域名变更后遗留的。"
                        f"请前往 **设置 → 集成 → Bemfa → 选项 → 删除同步** 进行清理。"
                    ),
                    "notification_id": "bemfa_orphan_topics",
                },
            )
        )

    async def async_create_sync(self, sync: Sync, user_input: dict[str, str]):
        """Create a topic to bemfa service and keep communication by mqtt.
        Except name, we store other config details in hass side.
        """
        sync.name = user_input.pop(OPTIONS_NAME)
        sync.config = user_input
        await self._bemfa_http.async_create_topic(sync.topic, sync.name)
        self._bemfa_mqtt.create_sync(sync)

    async def async_modify_sync(self, sync: Sync, user_input: dict[str, str]):
        """Modify topic and/or config of a sync."""
        name = user_input.pop(OPTIONS_NAME)
        if sync.name != name:
            sync.name = name
            await self._bemfa_http.async_rename_topic(sync.topic, name)
        if sync.config != user_input:
            sync.config = user_input
            self._bemfa_mqtt.modify_sync(sync)

    async def async_destroy_sync(self, topic: str):
        """Delete a topic from bemfa service and distroy mqtt communication."""
        await self._bemfa_http.async_del_topic(topic)
        self._bemfa_mqtt.destroy_sync(topic)

    async def async_stop(self) -> None:
        """Stop the service, called when Bemfa component stops."""
        await self._bemfa_mqtt.async_disconnect()
