"""Sensor platform for the Fröling Heater integration.

Creates all HA sensor entities from the data supplied by the coordinator:

  - One :class:`FroelingValueSensor` per discovered ValueSpec whose menu_type
    is MESSWERT, MESSWERT1 (measured values) or ANL_OUT (analogue outputs).
  - :class:`FroelingStateSensor`            – heater state text ("Heizen" …)
  - :class:`FroelingModeSensor`             – operating mode text
  - :class:`FroelingActiveErrorCountSensor` – count of ARRIVED (active) errors
  - :class:`FroelingLastErrorSensor`        – text of the most recent error

HA sensor platform docs:
  https://developers.home-assistant.io/docs/core/entity/sensor
"""

from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import UNIT_DEVICE_CLASS_MAP
from .coordinator import FroelingCoordinator
from .entity import FroelingEntity
from .pyfroeling import ErrorState
from .pyfroeling.const import MenuStructType

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create and register sensor entities for this config entry.

    Called once by HA when the integration is loaded.  The coordinator's
    ``data.specs`` list (populated during ``_async_setup``) determines which
    FroelingValueSensor instances are created.  The fixed "meta" sensors
    (state, mode, error count, last error) are always added.

    Parameters
    ----------
    hass:
        Home Assistant instance.
    entry:
        The config entry to set up sensors for.
    async_add_entities:
        Callback that registers the entity objects with HA.
    """
    # Retrieve the coordinator stored on the entry at integration setup time.
    coordinator: FroelingCoordinator = entry.runtime_data

    entities: list[FroelingEntity] = []

    # --- Dynamic value sensors from discovered specs ---
    for spec in coordinator.data.specs:
        menu_type = spec.menu_type

        if menu_type in (MenuStructType.MESSWERT, MenuStructType.MESSWERT1):
            # Standard measured sensor (temperature, pressure, %, …).
            entities.append(
                FroelingValueSensor(coordinator, spec.address, spec.title, spec.unit, sensor_type="VA")
            )

        elif menu_type == MenuStructType.ANL_OUT:
            # Analogue output channel – also exposed as a numeric sensor.
            entities.append(
                FroelingValueSensor(coordinator, spec.address, spec.title, spec.unit, sensor_type="AO")
            )

        # DIG_OUT and DIG_IN are handled by the binary_sensor platform.

    # --- Fixed "meta" sensors ---
    entities.append(FroelingStateSensor(coordinator))
    entities.append(FroelingModeSensor(coordinator))
    entities.append(FroelingActiveErrorCountSensor(coordinator))
    entities.append(FroelingLastErrorSensor(coordinator))

    _LOGGER.debug(
        "sensor.async_setup_entry: adding %d entities (%d value sensors + 4 meta)",
        len(entities), len(entities) - 4,
    )

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Value sensor (measured values and analogue outputs)
# ---------------------------------------------------------------------------

class FroelingValueSensor(FroelingEntity, SensorEntity):
    """Represents a single numeric sensor value from the heater.

    Covers both MESSWERT/MESSWERT1 (measured temperatures, pressures, …) and
    ANL_OUT (analogue output channels like pump speed).

    The unit and device class are set once at construction time from the
    ValueSpec metadata.  The native_value is read from coordinator data on
    each HA state write, rounded to 2 decimal places to avoid floating-point
    noise in the history graph.
    """

    def __init__(
        self,
        coordinator: FroelingCoordinator,
        address: int,
        name: str,
        unit: str,
        sensor_type: str = "VA",
    ) -> None:
        """Initialise the value sensor.

        Parameters
        ----------
        coordinator:
            Shared data coordinator for this config entry.
        address:
            16-bit register address of this sensor.
        name:
            German sensor title from the controller menu (preserved as-is).
        unit:
            Physical unit string (e.g. "°C", "%", "kW").
        sensor_type:
            Short category tag: "VA" for measured values, "AO" for analogue
            outputs.  Included in the unique_id to avoid collisions.
        """
        super().__init__(coordinator, sensor_type, address, name)

        # Physical unit as returned by the controller (e.g. "°C", "%").
        self._attr_native_unit_of_measurement = unit

        # Map the unit to an HA SensorDeviceClass if known; None otherwise.
        # Sensors with an unknown unit (e.g. "kW", "l/h") will still display
        # correctly – they just won't get the coloured history icon.
        self._attr_device_class = UNIT_DEVICE_CLASS_MAP.get(unit)

        # All numeric heater sensors are instantaneous measurements.
        self._attr_state_class = SensorStateClass.MEASUREMENT

        # Store address for data lookup in native_value.
        self._address = address

    @property
    def native_value(self) -> float | None:
        """Return the current sensor reading.

        Returns None if the sensor address is absent from coordinator data
        (e.g., the controller did not respond for this address during the
        last poll cycle).  HA will then show "Unavailable" for this entity.
        """
        if self.coordinator.data is None:
            return None

        sv = self.coordinator.data.values.get(self._address)
        if sv is None:
            return None

        # Round to 2 decimal places to avoid floating-point noise in history.
        return round(sv.value, 2)


# ---------------------------------------------------------------------------
# Fixed "meta" sensors
# ---------------------------------------------------------------------------

class FroelingStateSensor(FroelingEntity, SensorEntity):
    """Text sensor reporting the heater's current operating state.

    Reads from ``coordinator.data.status.state_text``, which is the German
    state name looked up from STATE_TABLE (e.g. "Heizen", "Abstellen Warten").
    This is the primary status sensor most users will display on their dashboard.
    """

    def __init__(self, coordinator: FroelingCoordinator) -> None:
        """Initialise the state sensor with a fixed virtual address 0x0001."""
        # Virtual address 0x0001 is chosen to avoid colliding with any real
        # controller address while still fitting the unique_id pattern.
        super().__init__(coordinator, "UD", 0x0001, "Heater State")

    @property
    def native_value(self) -> str | None:
        """Return the current state text (e.g. 'Heizen')."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.status.state_text


class FroelingModeSensor(FroelingEntity, SensorEntity):
    """Text sensor reporting the heater's current operating mode.

    Reads from ``coordinator.data.status.mode_text``, which carries the
    mode name decoded from the GET_STATE response (e.g. "Automatik").
    """

    def __init__(self, coordinator: FroelingCoordinator) -> None:
        """Initialise the mode sensor with a fixed virtual address 0x0002."""
        super().__init__(coordinator, "UD", 0x0002, "Operating Mode")

    @property
    def native_value(self) -> str | None:
        """Return the current operating mode text."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.status.mode_text


class FroelingActiveErrorCountSensor(FroelingEntity, SensorEntity):
    """Numeric sensor counting the number of currently active (ARRIVED) errors.

    An error is considered "active" when its :class:`~pyfroeling.ErrorState`
    bitmask includes the ARRIVED flag.  Errors that have been acknowledged or
    are already GONE are not counted.

    Typical use: alert automations trigger when this count goes above zero.
    """

    def __init__(self, coordinator: FroelingCoordinator) -> None:
        """Initialise the active-error-count sensor with virtual address 0x0001."""
        super().__init__(coordinator, "ERR", 0x0001, "Active Errors")
        # Icon that signals "something needs attention".
        self._attr_icon = "mdi:alert-circle"
        # Errors are countable, instantaneous measurements.
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        """Count and return errors that currently have the ARRIVED flag set."""
        if self.coordinator.data is None:
            return None

        return sum(
            1
            for err in self.coordinator.data.errors
            # Use bitmask test so combined states (e.g. ARRIVED | ACKNOWLEDGED = 3)
            # are also counted as active.  ErrorState.ARRIVED == 1.
            if err.state & ErrorState.ARRIVED
        )


class FroelingLastErrorSensor(FroelingEntity, SensorEntity):
    """Text sensor showing the message text of the most recent error entry.

    Returns the text of the first entry in the error log (index 0), which is
    the most recent error the controller has recorded.  Returns None when no
    errors are present.

    Useful on dashboards: the heater state binary sensor (PROBLEM) can trigger
    an alert while this sensor shows the reason.
    """

    def __init__(self, coordinator: FroelingCoordinator) -> None:
        """Initialise the last-error sensor with virtual address 0x0002."""
        super().__init__(coordinator, "ERR", 0x0002, "Last Error")
        self._attr_icon = "mdi:alert"

    @property
    def native_value(self) -> str | None:
        """Return the text of the first (most recent) error, or None."""
        if self.coordinator.data is None:
            return None

        errors = self.coordinator.data.errors
        if not errors:
            # No errors in the log – return None so HA shows "Unknown".
            return None

        return errors[0].text
