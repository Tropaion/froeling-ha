"""Base entity class for the Fröling Heater integration.

All Fröling entity subclasses (sensors, binary sensors) inherit from
:class:`FroelingEntity`.  It handles:

- Unique-ID generation (stable across HA restarts)
- Device grouping (all entities appear under one "Fröling Heater" device card)
- Coordinator subscription (coordinator push updates trigger state writes)

HA entity docs:
  https://developers.home-assistant.io/docs/core/entity
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME, DOMAIN
from .coordinator import FroelingCoordinator


class FroelingEntity(CoordinatorEntity[FroelingCoordinator]):
    """Base class shared by every Fröling entity.

    Inherits from :class:`CoordinatorEntity` so that entities automatically
    subscribe to coordinator updates and become unavailable when the coordinator
    raises :exc:`~homeassistant.helpers.update_coordinator.UpdateFailed`.

    All entity names are provided by the platform (via the ``name`` parameter)
    and are combined with the device name by HA because
    ``_attr_has_entity_name = True``.

    For entities that should use HA's translation system (Bug 3), pass a
    ``translation_key`` instead of a hardcoded ``name``.  When a translation
    key is provided, HA looks up the display name from the integration's
    strings.json / translations/*.json files, enabling full i18n support.

    Parameters
    ----------
    coordinator:
        The :class:`FroelingCoordinator` instance for this config entry.
    sensor_type:
        A short string identifying the entity category, e.g. ``"VA"``
        (sensor value), ``"AO"`` (analogue output), ``"ERR"`` (error
        counter), ``"UD"`` (user-data / state text).  Used in the unique_id
        so entities from different categories cannot collide.
    address:
        The 16-bit register or virtual address that distinguishes this entity
        from others of the same type.  For virtual entities (state text, error
        count) a synthetic address is chosen (0x0001, 0x0002, …).
    name:
        Human-readable entity name shown below the device card (e.g.
        "Kessel Ist").  Mutually exclusive with ``translation_key``.
    translation_key:
        HA translation key (e.g. ``"heater_state"``).  When provided,
        ``name`` is ignored and HA resolves the display name from the
        integration's translation files.  Use this for the fixed meta-entities
        so they appear in the user's language rather than hardcoded English.
    """

    # has_entity_name = False: entity names are used as-is without prepending
    # the device name. "Kesseltemperatur" stays "Kesseltemperatur", not
    # "Fröling P1 Kesseltemperatur". All entities are grouped under the
    # device anyway, so the prefix is redundant.
    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: FroelingCoordinator,
        sensor_type: str,
        address: int,
        name: str | None = None,
        translation_key: str | None = None,
    ) -> None:
        # Let CoordinatorEntity wire up the coordinator subscription.
        super().__init__(coordinator)

        # Build a stable unique_id from entry_id + type + address.
        # Using the config entry ID as a namespace ensures uniqueness even if
        # two heaters are added (they would have different entry IDs).
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{sensor_type}_0x{address:04x}"

        if translation_key is not None:
            # has_entity_name=False means translation_key doesn't work for names.
            # Look up the name from our own mapping instead.
            from .const import ENTITY_NAME_MAP
            self._attr_name = ENTITY_NAME_MAP.get(translation_key, translation_key)
        else:
            # Sensor names from the heater are already in German and are used
            # directly (e.g. "Kessel Ist", "Außentemperatur").
            self._attr_name = name

        # Retain type and address so subclasses can use them if needed.
        self._sensor_type = sensor_type
        self._address = address

    # ------------------------------------------------------------------
    # Device grouping
    # ------------------------------------------------------------------

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device this entity belongs to.

        All Fröling entities share the same :class:`DeviceInfo` keyed by
        ``(DOMAIN, entry_id)``.  HA uses this to group them under a single
        device card in the UI.

        The ``sw_version`` is populated from the firmware version string
        returned by the heater's GET_VERSION command (coordinator.data.status).
        If no data has been fetched yet (first startup), a placeholder is used.
        """
        # Firmware version from the last successful poll.  May be None during
        # the very first setup before async_config_entry_first_refresh returns.
        fw_version: str | None = None
        if self.coordinator.data is not None:
            fw_version = self.coordinator.data.status.version

        entry_id = self.coordinator.config_entry.entry_id

        return DeviceInfo(
            # Unique device identifier keyed to this config entry.
            identifiers={(DOMAIN, entry_id)},
            # Friendly device name from user input during setup.
            name=self.coordinator.config_entry.data.get(
                CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME
            ),
            # The manufacturer name as it appears on the physical device.
            manufacturer="Fröling",
            # Controller board name (Lambdatronic P 3200 is the pellet-specific
            # variant; the S 3200 is the wood-gasifier variant – both share
            # the same COM1 protocol).
            model="Lambdatronic P 3200",
            # Firmware version from GET_VERSION response.
            sw_version=fw_version,
        )
