"""Support for bemfa service."""
from __future__ import annotations
import asyncio

import logging
from typing import Any

import paho.mqtt.client as mqtt

from homeassistant.const import (
    EVENT_STATE_CHANGED,
)
from homeassistant.core import HomeAssistant

from .const import (
    INTERVAL_PING_RECEIVE,
    INTERVAL_PING_SEND,
    MAX_PING_LOST,
    MQTT_HOST,
    MQTT_KEEPALIVE,
    MQTT_PORT,
    TOPIC_PING,
    TOPIC_PUBLISH,
)

from .sync import Sync

_LOGGING = logging.getLogger(__name__)


class BemfaMqtt:
    """Set up mqtt connections to bemfa service, subscribe topcs and publish messages."""

    def __init__(
        self, hass: HomeAssistant, uid: str, entity_ids: list[str] | None
    ) -> None:
        """Initialize."""
        self._hass = hass

        # Init MQTT connection
        self._mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, uid, mqtt.MQTTv311)

        self._topic_to_sync: dict[str, Sync] = {}

        self._remove_listener: Any = None
        self._ping_task: asyncio.Task | None = None
        self._ping_lost: int = 0
        self._running: bool = False

    def create_sync(self, sync: Sync):
        """Add an topic to our watching list."""
        self._topic_to_sync[sync.topic] = sync
        self._mqttc.publish(
            TOPIC_PUBLISH.format(topic=sync.topic),
            sync.generate_msg(),
        )
        self._mqttc.subscribe(sync.topic, 1)

    def modify_sync(self, sync: Sync):
        """Modify a sync."""
        if sync.topic in self._topic_to_sync:
            self._topic_to_sync[sync.topic] = sync
            self._mqttc.publish(
                TOPIC_PUBLISH.format(topic=sync.topic),
                sync.generate_msg(),
            )

    def destroy_sync(self, topic: str):
        """Remove an topic from our watching list."""
        if topic in self._topic_to_sync:
            self._topic_to_sync.pop(topic)
        self._mqttc.unsubscribe(topic)

    async def async_connect(self) -> None:
        """Connect to Bemfa service asynchronously to avoid blocking the event loop."""
        self._running = True

        # Run synchronous MQTT connect in executor to avoid blocking HA event loop
        await asyncio.get_event_loop().run_in_executor(
            None, self._mqttc.connect, MQTT_HOST, MQTT_PORT, MQTT_KEEPALIVE
        )

        self._mqttc.on_message = self._mqtt_on_message
        self._mqttc.loop_start()

        # Start heartbeat as a managed task (replaces recursive _ping)
        self._ping_task = asyncio.ensure_future(self._ping_loop())

        # Listen for state changes
        self._remove_listener = self._hass.bus.async_listen(
            EVENT_STATE_CHANGED, self._state_listener
        )

        # Listen for heartbeat packages
        self._mqttc.subscribe(TOPIC_PING, 1)

        _LOGGING.info("Connected to Bemfa MQTT broker")

    async def _ping_loop(self) -> None:
        """Heartbeat loop using while-loop instead of recursion to prevent task accumulation."""
        while self._running:
            await asyncio.sleep(INTERVAL_PING_SEND)

            if not self._running:
                break

            self._mqttc.publish(TOPIC_PING, "ping")

            # Wait for ping response
            try:
                await asyncio.wait_for(
                    self._wait_for_ping_response(),
                    timeout=INTERVAL_PING_RECEIVE,
                )
                # Ping response received, reset counter
                self._ping_lost = 0
            except asyncio.TimeoutError:
                # No ping response within timeout
                self._ping_lost += 1
                _LOGGING.warning(
                    "Bemfa ping lost (%d/%d)", self._ping_lost, MAX_PING_LOST
                )
                if self._ping_lost >= MAX_PING_LOST:
                    _LOGGING.warning("Bemfa MQTT connection lost, reconnecting...")
                    self._ping_lost = 0
                    await self._async_reconnect()

    async def _wait_for_ping_response(self) -> None:
        """Wait for a ping response from the MQTT broker."""
        # This is a simple approach: the _mqtt_on_message callback will
        # set an event when a ping response is received.
        self._ping_received = asyncio.Event()
        await self._ping_received.wait()

    def _notify_ping_received(self) -> None:
        """Called from _mqtt_on_message when a ping response is received."""
        if hasattr(self, "_ping_received") and self._ping_received is not None:
            self._ping_received.set()

    async def _async_reconnect(self) -> None:
        """Reconnect to Bemfa service asynchronously."""
        self._stop_internal()

        try:
            await self.async_connect()
            # Re-subscribe all existing syncs
            for sync in list(self._topic_to_sync.values()):
                self.create_sync(sync)
            _LOGGING.info("Reconnected and re-subscribed %d syncs", len(self._topic_to_sync))
        except Exception:  # noqa: BLE001
            _LOGGING.error("Failed to reconnect to Bemfa MQTT broker")

    def _stop_internal(self) -> None:
        """Stop internal timers and connections without affecting sync state."""
        self._running = False

        # Cancel ping task
        if self._ping_task is not None:
            self._ping_task.cancel()
            self._ping_task = None

        # Unlisten for state changes
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

        # Stop MQTT
        try:
            self._mqttc.loop_stop()
            self._mqttc.disconnect()
        except Exception:  # noqa: BLE001
            pass

    async def async_disconnect(self) -> None:
        """Disconnect from Bemfa service asynchronously."""
        self._stop_internal()
        _LOGGING.info("Disconnected from Bemfa MQTT broker")

    def _state_listener(self, event):
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        entity_id = new_state.entity_id
        for (topic, sync) in self._topic_to_sync.items():
            if entity_id in sync.get_watched_entity_ids():
                self._mqttc.publish(
                    TOPIC_PUBLISH.format(topic=topic),
                    sync.generate_msg(),
                )

    def _mqtt_on_message(self, _mqtt_client, _userdata, message) -> None:
        if message.topic == TOPIC_PING:
            self._notify_ping_received()
            return

        if message.topic in self._topic_to_sync:
            payload = message.payload.decode()
            _LOGGING.debug(
                "Received MQTT message on topic %s: %s", message.topic, payload
            )
            self._topic_to_sync[message.topic].resolve_msg(payload)
        else:
            _LOGGING.debug(
                "Received MQTT message on unregistered topic %s", message.topic
            )
