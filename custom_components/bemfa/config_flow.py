"""Config flow for bemfa integration."""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .sync import Sync
from .const import (
    BEMFA_TYPE_MAP,
    CONF_UID,
    DOMAIN,
    DOMAIN_TYPE_MAP,
    OPTIONS_CONFIG,
    OPTIONS_NAME,
    OPTIONS_SELECT,
    OPTIONS_TYPE,
    TYPE_OVERRIDES_KEY,
    TopicSuffix,
)
from .service import BemfaService

_LOGGER = logging.getLogger(__name__)

# Domains that Bemfa can sync to
_SUPPORTED_DOMAINS = [
    "light", "switch", "cover", "fan",
    "climate", "sensor", "binary_sensor",
]

# Type options list (shared between create and modify)
_TYPE_OPTIONS = [
    SelectOptionDict(value=suffix, label=label)
    for suffix, label in BEMFA_TYPE_MAP.items()
]

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_UID): str,
    }
)


def _get_default_type_suffix(entity_id: str) -> str:
    """Get the default Bemfa type suffix for a given HA entity_id."""
    domain = entity_id.split(".")[0]
    return DOMAIN_TYPE_MAP.get(domain, TopicSuffix.SWITCH)


def _get_effective_type_suffix(sync: Sync, type_overrides: dict[str, str]) -> str:
    """Get the effective Bemfa type suffix for a sync, considering overrides."""
    if sync.entity_id in type_overrides:
        return type_overrides[sync.entity_id]
    return _get_default_type_suffix(sync.entity_id)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for bemfa."""

    VERSION = 1

    # Bemfa service uses uid to auth api calls. One shall provide his uid to config this integration.
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA, last_step=True
            )

        # uid should match this regExp
        if not re.match("^[0-9a-f]{32}$", user_input[CONF_UID]):
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={"base": "invalid_uid"},
                last_step=True,
            )

        # Multiply integration instances with same uid may case unexpected results.
        # We treat the md5sum of each configured uid as unique.
        uid_md5 = hashlib.md5(user_input[CONF_UID].encode("utf-8")).hexdigest()
        await self.async_set_unique_id(uid_md5)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title="",
            data=user_input,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Create the options flow."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle a option flow for bemfa."""

    # creat or modify a sync
    _is_create: bool

    # a dict to hold syncs when create / modify one of them
    # with this map we can get it in the next step
    _sync_dict: dict[str, Sync]

    # current sync we are creating or modify
    _sync: Sync

    # all syncs selected for batch creation
    _selected_syncs: list[Sync]

    # queue of complex syncs that need individual config after name page
    _pending_syncs: list[Sync]

    # entity IDs that are already synced (for exclusion)
    _synced_entity_ids: list[str]

    # type overrides: entity_id → Bemfa type suffix
    _type_overrides: dict[str, str]

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._entry_id = config_entry.entry_id
        self._config = (
            config_entry.options[OPTIONS_CONFIG].copy()
            if OPTIONS_CONFIG in config_entry.options
            else {}
        )
        self._type_overrides = (
            config_entry.options[TYPE_OVERRIDES_KEY].copy()
            if TYPE_OVERRIDES_KEY in config_entry.options
            else {}
        )
        self._pending_syncs = []
        self._selected_syncs = []
        self._synced_entity_ids = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "create_sync",
                "modify_sync",
                "destroy_sync",
            ],
        )

    def _apply_type_overrides(self, all_syncs: list[Sync]) -> None:
        """Apply stored type overrides to sync objects.

        This must be called before comparing sync.topic with Bemfa cloud
        topics, so that overridden entities generate the correct topic name.
        """
        for sync in all_syncs:
            if sync.entity_id in self._type_overrides:
                sync.topic_suffix = self._type_overrides[sync.entity_id]

    # ================================================================
    # CREATE sync flow
    # ================================================================

    async def async_step_create_sync(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Create hass-to-bemfa sync(s). Supports multi-select with search."""
        if user_input is not None:
            selected_ids = user_input[OPTIONS_SELECT]
            # Ensure list (single select returns str)
            if isinstance(selected_ids, str):
                selected_ids = [selected_ids]

            if not selected_ids:
                return self.async_show_form(
                    step_id="create_sync",
                    errors={"base": "no_selection"},
                    data_schema=self._build_create_schema(),
                    last_step=False,
                )

            self._is_create = True

            # Build Sync objects for selected entities
            service = self._get_service()
            all_syncs = service.collect_supported_syncs()
            self._apply_type_overrides(all_syncs)
            sync_by_eid = {s.entity_id: s for s in all_syncs}

            self._selected_syncs = []
            skipped = []
            for eid in selected_ids:
                if eid in sync_by_eid:
                    self._selected_syncs.append(sync_by_eid[eid])
                else:
                    skipped.append(eid)

            if skipped:
                _LOGGER.warning(
                    "Skipping entities not supported by Bemfa: %s", skipped
                )

            if not self._selected_syncs:
                return self.async_show_form(
                    step_id="create_sync",
                    errors={"base": "no_selection"},
                    data_schema=self._build_create_schema(),
                    last_step=False,
                )

            return await self.async_step_create_sync_names()

        service = self._get_service()
        all_topics = await service.async_fetch_all_topics()
        all_syncs = service.collect_supported_syncs()
        self._apply_type_overrides(all_syncs)
        self._sync_dict = {}
        for sync in all_syncs:
            if sync.topic not in all_topics:
                self._sync_dict[sync.entity_id] = sync

        if not bool(self._sync_dict):
            return self.async_show_form(step_id="empty", last_step=False)

        self._synced_entity_ids = [
            s.entity_id for s in all_syncs if s.topic in all_topics
        ]

        return self.async_show_form(
            step_id="create_sync",
            description_placeholders={
                "hint": "可多选实体批量添加，支持搜索。输入关键字实时过滤实体。"
            },
            data_schema=self._build_create_schema(),
            last_step=False,
        )

    def _build_create_schema(self) -> vol.Schema:
        """Build the schema for the create sync selection step."""
        return vol.Schema(
            {
                vol.Optional(OPTIONS_SELECT, default=[]): EntitySelector(
                    EntitySelectorConfig(
                        domain=_SUPPORTED_DOMAINS,
                        multiple=True,
                        exclude_entities=self._synced_entity_ids,
                    )
                )
            }
        )

    async def async_step_create_sync_names(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Set names and types for all selected entities on one page.

        Uses indexed keys (名称_N, 类型_N) for clean display, with a
        Markdown reference table in the description showing entity details.
        """
        if user_input is not None:
            service = self._get_service()

            simple_syncs: list[Sync] = []
            complex_syncs: list[Sync] = []

            for i, sync in enumerate(self._selected_syncs, 1):
                # Look up by indexed keys
                name = user_input.get(f"名称_{i}", sync.name) or sync.name
                sync.name = name

                # Check for type override
                default_suffix = _get_default_type_suffix(sync.entity_id)
                selected_type = user_input.get(f"类型_{i}", default_suffix)

                if selected_type and selected_type != default_suffix:
                    sync.topic_suffix = selected_type
                    self._type_overrides[sync.entity_id] = selected_type
                elif sync.entity_id in self._type_overrides:
                    del self._type_overrides[sync.entity_id]

                if len(sync.generate_details_schema()) <= 1:
                    simple_syncs.append(sync)
                else:
                    complex_syncs.append(sync)

            for sync in simple_syncs:
                await service.async_create_sync(sync, {OPTIONS_NAME: sync.name})
                if sync.config:
                    self._config[sync.topic] = sync.config

            if complex_syncs:
                self._pending_syncs = complex_syncs[1:]
                self._sync = complex_syncs[0]
                return await self._async_step_sync_config()

            return self.async_create_entry(
                title="",
                data={
                    OPTIONS_CONFIG: self._config,
                    TYPE_OVERRIDES_KEY: self._type_overrides,
                },
            )

        # Build Markdown reference table for the description
        header = "| 序号 | 实体名称 | HA 类型 | 默认巴法云类型 |"
        sep = "|:----:|----------|---------|---------------|"
        rows = []
        for i, sync in enumerate(self._selected_syncs, 1):
            domain = sync.entity_id.split(".")[0]
            default_suffix = _get_default_type_suffix(sync.entity_id)
            default_type_name = BEMFA_TYPE_MAP.get(default_suffix, default_suffix)
            rows.append(f"| {i} | {sync.name} | {domain} | {default_type_name} |")

        table = f"{header}\n{sep}\n" + "\n".join(rows)

        complex_count = sum(
            1 for s in self._selected_syncs
            if len(s.generate_details_schema()) > 1
        )
        hint_parts = [table]
        hint_parts.append("\n\n类型决定巴法云中设备分类，影响语音控制命令。")
        if complex_count:
            hint_parts.append(
                f"（其中 {complex_count} 个复杂类型还需单独配置参数）"
            )

        # Build schema: indexed name + type per entity
        schema_fields = {}
        for i, sync in enumerate(self._selected_syncs, 1):
            # Name input
            schema_fields[vol.Optional(f"名称_{i}", default=sync.name)] = str
            # Type selector
            effective_suffix = _get_effective_type_suffix(sync, self._type_overrides)
            schema_fields[
                vol.Optional(f"类型_{i}", default=effective_suffix)
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=_TYPE_OPTIONS,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        return self.async_show_form(
            step_id="create_sync_names",
            data_schema=vol.Schema(schema_fields),
            description_placeholders={"hint": "".join(hint_parts)},
        )

    # ================================================================
    # MODIFY sync flow
    # ================================================================

    async def async_step_modify_sync(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Modify a hass-to-bemfa sync."""
        if user_input is not None:
            self._sync = self._sync_dict[user_input[OPTIONS_SELECT]]
            return await self._async_step_sync_config()

        service = self._get_service()
        all_topics = await service.async_fetch_all_topics()
        all_syncs = service.collect_supported_syncs()
        self._apply_type_overrides(all_syncs)
        self._sync_dict = {}
        for sync in all_syncs:
            if sync.topic in all_topics:
                sync.name = all_topics[sync.topic]
                self._sync_dict[sync.entity_id] = sync

        if not bool(self._sync_dict):
            return self.async_show_form(step_id="empty", last_step=False)

        self._is_create = False

        return self.async_show_form(
            step_id="modify_sync",
            data_schema=vol.Schema(
                {
                    vol.Required(OPTIONS_SELECT): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=sync.entity_id,
                                    label=sync.generate_option_label(),
                                )
                                for sync in self._sync_dict.values()
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            last_step=False,
        )

    async def _async_step_sync_config(self) -> FlowResult:
        """Set details of a hass-to-bemfa sync.

        For modify, includes a type selector so users can change
        the Bemfa device type (e.g. switch → light).
        """
        if self._sync.topic in self._config:
            self._sync.config = self._config[self._sync.topic]

        # Build schema: type selector (for modify) + original schema fields
        schema_dict: dict[Any, Any] = {}

        # Add type selector for modify flow
        if not self._is_create:
            effective_suffix = _get_effective_type_suffix(
                self._sync, self._type_overrides
            )
            schema_dict[vol.Optional(OPTIONS_TYPE, default=effective_suffix)] = (
                SelectSelector(
                    SelectSelectorConfig(
                        options=_TYPE_OPTIONS,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            )

        # Add original schema fields (name + extra params)
        schema_dict.update(self._sync.generate_details_schema())

        # Show remaining count if there are more syncs queued
        description = None
        if self._pending_syncs:
            description = f"还有 {len(self._pending_syncs)} 个实体待配置"

        return self.async_show_form(
            step_id=self._sync.get_config_step_id(),
            data_schema=vol.Schema(schema_dict),
            description_placeholders={"hint": description} if description else None,
        )

    async def async_step_sync_config_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Set details of a hass-to-bemfa sensor sync."""
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_binary_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Set details of a hass-to-bemfa binary sensor sync."""
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_climate(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Set details of a hass-to-bemfa climate sync."""
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_cover(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Set details of a hass-to-bemfa cover sync."""
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_fan(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Set details of a hass-to-bemfa fan sync."""
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_light(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Set details of a hass-to-bemfa light sync."""
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Set details of a hass-to-bemfa switch sync."""
        return await self._async_step_sync_config_done(user_input)

    async def _async_step_sync_config_done(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Process sync config form submission.

        Handles type changes for modify: if the Bemfa device type changed,
        the old topic is deleted and a new one is created with the new suffix.
        """
        service = self._get_service()

        # Check for type change (only relevant for modify)
        if not self._is_create and OPTIONS_TYPE in user_input:
            new_type = user_input.pop(OPTIONS_TYPE)
            old_suffix = _get_effective_type_suffix(self._sync, self._type_overrides)
            default_suffix = _get_default_type_suffix(self._sync.entity_id)

            if new_type != old_suffix:
                # Type changed! Need to delete old topic and create new one
                old_topic = self._sync.topic
                old_config = self._config.pop(old_topic, {})

                # Delete old sync (topic + MQTT subscription)
                await service.async_destroy_sync(old_topic)

                # Update the type override
                if new_type != default_suffix:
                    self._type_overrides[self._sync.entity_id] = new_type
                elif self._sync.entity_id in self._type_overrides:
                    del self._type_overrides[self._sync.entity_id]

                # Apply new type suffix (this changes sync.topic)
                self._sync.topic_suffix = new_type

                # Create new sync with the new topic
                await service.async_create_sync(self._sync, user_input)

                # Migrate old config to new topic key
                if old_config:
                    self._config[self._sync.topic] = old_config
                if self._sync.config:
                    self._config[self._sync.topic] = self._sync.config

                # Handle pending syncs or finish
                if self._pending_syncs:
                    self._sync = self._pending_syncs.pop(0)
                    return await self._async_step_sync_config()

                return self.async_create_entry(
                    title="",
                    data={
                        OPTIONS_CONFIG: self._config,
                        TYPE_OVERRIDES_KEY: self._type_overrides,
                    },
                )

        # Normal flow (no type change, or create)
        if self._is_create:
            await service.async_create_sync(self._sync, user_input)
        else:
            await service.async_modify_sync(self._sync, user_input)

        # store config to integration options
        if self._sync.config:
            self._config[self._sync.topic] = self._sync.config
        elif self._sync.topic in self._config:
            self._config.pop(self._sync.topic)

        # If there are more syncs pending, continue with the next one
        if self._pending_syncs:
            self._sync = self._pending_syncs.pop(0)
            return await self._async_step_sync_config()

        return self.async_create_entry(
            title="",
            data={
                OPTIONS_CONFIG: self._config,
                TYPE_OVERRIDES_KEY: self._type_overrides,
            },
        )

    # ================================================================
    # DESTROY sync flow
    # ================================================================

    async def async_step_destroy_sync(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Destroy hass-to-bemfa sync(s)"""
        service = self._get_service()
        if user_input is not None:
            selected_ids = user_input[OPTIONS_SELECT]
            if isinstance(selected_ids, str):
                selected_ids = [selected_ids]

            # Build reverse map: topic → entity_id (with type overrides applied)
            all_syncs_full = service.collect_supported_syncs()
            self._apply_type_overrides(all_syncs_full)
            topic_to_eid = {s.topic: s.entity_id for s in all_syncs_full}

            for topic in selected_ids:
                await service.async_destroy_sync(topic)
                if topic in self._config:
                    self._config.pop(topic)
                # Remove type override for the entity whose topic was destroyed
                if topic in topic_to_eid:
                    eid = topic_to_eid[topic]
                    if eid in self._type_overrides:
                        del self._type_overrides[eid]

            return self.async_create_entry(
                title="",
                data={
                    OPTIONS_CONFIG: self._config,
                    TYPE_OVERRIDES_KEY: self._type_overrides,
                },
            )

        all_topics = await service.async_fetch_all_topics()
        all_syncs = service.collect_supported_syncs()
        self._apply_type_overrides(all_syncs)
        topic_map: dict[str, str] = {}
        for sync in all_syncs:
            if sync.topic in all_topics:
                sync.name = all_topics[sync.topic]
                all_topics.pop(sync.topic)
                topic_map[sync.topic] = sync.generate_option_label()

        for (topic, name) in all_topics.items():
            topic_map[topic] = "[?] {name}".format(name=name)

        if not bool(topic_map):
            return self.async_show_form(step_id="empty", last_step=False)
        return self.async_show_form(
            step_id="destroy_sync",
            data_schema=vol.Schema(
                {
                    vol.Optional(OPTIONS_SELECT, default=[]): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=value,
                                    label=label,
                                )
                                for (value, label) in topic_map.items()
                            ],
                            mode=SelectSelectorMode.LIST,
                            multiple=True,
                        )
                    )
                }
            ),
        )

    async def async_step_empty(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """No syncs found."""
        return await self.async_step_init(user_input)

    def _get_service(self) -> BemfaService:
        return self.hass.data[DOMAIN].get(self._entry_id)["service"]
