"""Select platform for writable choice parameters (mstParDig, 0x08).

Creates SelectEntity instances for parameters that use a small integer range
to represent a set of discrete options (e.g. operating mode: 0=Off, 1=Auto,
2=Manual).

The heater does not supply text labels for individual option values in the
binary protocol – the labels exist only on the controller's own LCD display.
For now, options are displayed as their numeric string representations
("0", "1", "2", …).  Users can create template entities or automations to
map these numbers to meaningful labels if desired.

Selection criteria (complementary to the number platform):
- max_value – min_value <= 10  (small integer choice range)
- digits == 0                  (no decimal places – whole-number selection)
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_SELECTED_PARAMETERS, CONF_WRITE_ENABLED
from .coordinator import FroelingCoordinator
from .entity import FroelingEntity
from .pyfroeling import WritableParameter

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create and register SelectEntity instances for writable choice parameters.

    Only runs when write mode was enabled during the config flow.  Reads the
    list of selected parameter addresses from the config entry and creates one
    entity per parameter that qualifies as a choice (small integer range,
    integer step – the complementary set to what the number platform handles).

    Parameters
    ----------
    hass:
        Home Assistant instance.
    entry:
        The config entry to set up select entities for.
    async_add_entities:
        Callback that registers the entity objects with HA.
    """
    # Skip this platform entirely if write mode was not enabled
    if not entry.data.get(CONF_WRITE_ENABLED, False):
        _LOGGER.debug("select.async_setup_entry: write mode disabled, skipping")
        return

    coordinator: FroelingCoordinator = entry.runtime_data

    # Parse the selected parameter addresses (stored as hex strings)
    selected_addrs: set[int] = set()
    for addr_str in entry.data.get(CONF_SELECTED_PARAMETERS, []):
        try:
            selected_addrs.add(int(addr_str, 16))
        except (ValueError, TypeError):
            pass

    if not selected_addrs:
        _LOGGER.debug("select.async_setup_entry: no selected parameters")
        return

    entities: list[FroelingSelectEntity] = []

    # Iterate over parameters that were actually read on the first poll
    for addr, param in coordinator.data.parameters.items():
        if addr not in selected_addrs:
            continue  # User didn't select this parameter

        # Only create a select entity for parameters with a small integer range.
        # This is the complement of the number platform's numeric range check:
        #   - max - min <= 10  → select (discrete choices)
        #   - max - min > 10   → number (continuous range)
        #   - digits > 0       → number (fractional values)
        value_range = param.max_value - param.min_value
        is_choice = value_range <= 10 and param.digits == 0

        if not is_choice:
            # Will be handled by the number platform
            _LOGGER.debug(
                "select: skipping 0x%04X '%s' (range=%s, digits=%d) – defer to number",
                addr, param.title, value_range, param.digits,
            )
            continue

        # Register this parameter's title with the coordinator
        coordinator.set_parameter_title(addr, param.title)

        entities.append(FroelingSelectEntity(coordinator, param))

    _LOGGER.debug(
        "select.async_setup_entry: adding %d select entities", len(entities)
    )
    async_add_entities(entities)


# ---------------------------------------------------------------------------
# SelectEntity
# ---------------------------------------------------------------------------

class FroelingSelectEntity(FroelingEntity, SelectEntity):
    """A drop-down select entity for a single writable heater choice parameter.

    Options are the integer values from min_value to max_value (inclusive),
    displayed as strings ("0", "1", "2", …).  Writing a value back converts
    the option string to a float and delegates to the coordinator.

    The heater's LCD may show German text labels for each option value (e.g.
    "Automatik" for mode 0).  Those labels are not available in the binary
    protocol response, so we show numbers.  The user can create a HA template
    entity on top of this one to apply friendly labels.
    """

    def __init__(
        self,
        coordinator: FroelingCoordinator,
        param: WritableParameter,
    ) -> None:
        """Initialise the select entity.

        Parameters
        ----------
        coordinator:
            Shared data coordinator for this config entry.
        param:
            The :class:`~pyfroeling.WritableParameter` this entity represents.
        """
        # Use "SR" (Select Register) as the sensor type tag to avoid unique_id
        # collisions with "VA" (sensor) and "NR" (number) entities.
        super().__init__(coordinator, "SR", param.address, param.title)

        # Store the parameter address for coordinator data lookups
        self._address = param.address

        # Build the option list from the allowed integer range.
        # min_value and max_value are floats but represent whole-number choices;
        # convert to int to generate clean option strings like "0", "1", "2".
        low = int(param.min_value)
        high = int(param.max_value)
        # options must be strings (HA SelectEntity contract)
        self._attr_options = [str(i) for i in range(low, high + 1)]

        # Remember the factor for encode/decode during set operations.
        # Choice parameters usually have factor=1 (they are raw integer codes)
        # but we honour whatever the heater reports.
        self._factor = param.factor

    @property
    def current_option(self) -> str | None:
        """Return the currently active option as a string.

        Reads the current value from the coordinator snapshot, converts it to
        an integer, then returns its string representation.  Returns None if
        the parameter is absent (coordinator has no data yet or read failed).
        """
        if self.coordinator.data is None:
            return None
        wp = self.coordinator.data.parameters.get(self._address)
        if wp is None:
            return None
        # Convert float value to integer string (choice parameters have digits=0)
        return str(int(wp.value))

    async def async_select_option(self, option: str) -> None:
        """Write the selected option to the heater.

        Called by HA when the user picks a new option in the UI or an
        automation calls select.select_option.

        Parameters
        ----------
        option:
            The option string (e.g. "2") chosen by the user.  Converted to
            a float before being sent to the coordinator.
        """
        _LOGGER.debug(
            "FroelingSelectEntity: setting 0x%04X '%s' to option '%s'",
            self._address, self.name, option,
        )
        # Convert the option string (e.g. "2") to a float for the coordinator
        value = float(option)
        confirmed = await self.coordinator.async_write_parameter(
            self._address, value, self._factor
        )
        _LOGGER.debug(
            "FroelingSelectEntity: 0x%04X confirmed = %s", self._address, confirmed
        )
