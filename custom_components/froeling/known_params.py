"""Known parameter address table for Fröling Lambdatronic controllers.

This table provides:
1. English translations for parameter names (heater sends German only)
2. Basic/Expert categorization (only basic params shown by default)
3. Option labels for select entities (e.g., Betriebsart values)

Parameters NOT in this table are automatically categorized as "expert"
and hidden by default in the setup flow.

Addresses are from a Fröling P1 Pellet with firmware 50.04.04.11.
Other Lambdatronic 3200 models may have different addresses -- unknown
addresses are always treated as expert.
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class KnownParameter:
    """Metadata for a known parameter address."""
    en: str                                    # English name
    de: str = ""                               # German name (empty = use heater's name)
    options: dict[int, str] = field(default_factory=dict)  # Value -> label mapping


# ---------------------------------------------------------------------------
# Known basic parameters (shown by default in setup)
# Addresses from Fröling P1, firmware 50.04.04.11
# ---------------------------------------------------------------------------

KNOWN_BASIC_PARAMS: dict[int, KnownParameter] = {
    # --- System ---
    # Betriebsart mapping verified on P1 firmware 50.04.04.11:
    # Setting value 1 → heater shows "Sommerbetrieb"
    # Setting value 2 → heater shows "Übergangsbetrieb"
    # Therefore: 0 = Winterbetrieb, 1 = Sommerbetrieb, 2 = Übergangsbetrieb
    # (Winter is the "default" full-heating mode at value 0)
    0x02F5: KnownParameter(
        en="Operating Mode",
        de="Betriebsart",
        options={0: "Winterbetrieb", 1: "Sommerbetrieb", 2: "Übergangsbetrieb"},
    ),

    # --- Boiler ---
    0x01E0: KnownParameter(en="Boiler Target Temperature", de="Kessel-Solltemperatur"),
    0x0000: KnownParameter(en="Shutdown if boiler temp exceeds target +",
                           de="Abstellen wenn Kesseltemperatur höher als Solltemperatur +"),
    0x01E1: KnownParameter(en="Min. boiler temp for pumps to run",
                           de="Kesseltemperatur, ab der alle Pumpen laufen dürfen"),
    0x0201: KnownParameter(en="STB sleeve temp for pumps to run",
                           de="Temperatur in der STB Hülse, ab der alle Pumpen laufen"),

    # --- Heating circuit ---
    0x004A: KnownParameter(en="Heating circuit boost (sliding mode)",
                           de="Heizkreisüberhöhung bei gleitendem Betrieb"),
    0x004B: KnownParameter(en="Sliding mode active",
                           de="Gleitender Betrieb aktiv",
                           options={0: "Aus", 1: "Ein"}),
    0x004C: KnownParameter(en="Desired room temperature (heating)",
                           de="Gewünschte Raumtemperatur während des Heizbetriebs"),

    # --- Heating circuit temperatures (circuit 1 addresses) ---
    0x0057: KnownParameter(en="Flow temp at -10°C outside", de="Vorlauftemperatur bei -10°C"),
    0x0058: KnownParameter(en="Flow temp at +10°C outside", de="Vorlauftemperatur bei +10°C"),
    0x004F: KnownParameter(en="Flow temp reduction (setback mode)",
                           de="Absenkung der Vorlauftemperatur im Absenkbetrieb"),
    0x0050: KnownParameter(en="Pump off above outside temp (heating)",
                           de="Außentemperatur Pumpe aus (Heizbetrieb)"),
    0x006E: KnownParameter(en="Max. flow temperature", de="Maximale Vorlauftemperatur"),
    0x0080: KnownParameter(en="Frost protection temperature", de="Frostschutztemperatur"),

    # --- Boiler 1 (DHW) ---
    0x0153: KnownParameter(en="DHW target temperature (Boiler 1)",
                           de="Gewünschte Boilertemperatur"),
    0x0156: KnownParameter(en="DHW reload below (Boiler 1)",
                           de="Nachladen, wenn Boilertemperatur unter"),
    0x0155: KnownParameter(en="Use residual heat (Boiler 1)",
                           de="Restwärmenutzung",
                           options={0: "Aus", 1: "Ein"}),
    0x0157: KnownParameter(en="Charge DHW only once per day (Boiler 1)",
                           de="Boiler nur einmal pro Tag aufladen",
                           options={0: "Aus", 1: "Ein"}),
    0x0158: KnownParameter(en="Legionella heating active (Boiler 1)",
                           de="Legionelle Aufheizung aktiv",
                           options={0: "Aus", 1: "Ein"}),

    # --- Boiler 2 (DHW) ---
    0x0160: KnownParameter(en="DHW target temperature (Boiler 2)",
                           de="Gewünschte Boilertemperatur (Boiler 2)"),
    0x0163: KnownParameter(en="DHW reload below (Boiler 2)",
                           de="Nachladen (Boiler 2)"),
    0x0162: KnownParameter(en="Use residual heat (Boiler 2)",
                           de="Restwärmenutzung (Boiler 2)",
                           options={0: "Aus", 1: "Ein"}),
    0x0165: KnownParameter(en="Legionella heating active (Boiler 2)",
                           de="Legionelle (Boiler 2)",
                           options={0: "Aus", 1: "Ein"}),

    # --- Solar ---
    0x01E8: KnownParameter(en="Solar system type", de="Solar-System",
                           options={1: "Puffer", 2: "Boiler", 3: "Puffer + Boiler"}),
    0x01E9: KnownParameter(en="Collector switch-on difference", de="Kollektor Einschalt-Differenz"),
    0x01EA: KnownParameter(en="Collector switch-off difference", de="Kollektor Ausschalt-Differenz"),
    0x01EB: KnownParameter(en="Max. buffer temp (solar charging)",
                           de="Maximale Puffertemperatur unten bei Solarladung"),
    0x01EC: KnownParameter(en="DHW target temp (solar charging)",
                           de="Boiler-Solltemperatur bei Solarladung"),

    # --- Follow-up boiler ---
    0x01E3: KnownParameter(en="Follow-up boiler switch-on delay",
                           de="Einschaltverzögerung des Folgekessels"),
    0x01E4: KnownParameter(en="Start follow-up if buffer top below",
                           de="Start Folgekessel wenn Puffertemperatur unter"),
    0x01E5: KnownParameter(en="Min. runtime of follow-up boiler",
                           de="Minimale Laufzeit des Folgekessel"),
    0x01E6: KnownParameter(en="Min. temperature of follow-up boiler",
                           de="Minimaltemperatur des Folgekessel"),
    0x01E7: KnownParameter(en="Temp difference follow-up / buffer",
                           de="Temperaturdifferenz Folgekessel und Puffer"),

    # --- Heating circuits: on/off ---
    0x0306: KnownParameter(en="Heating circuit 1 active",
                           de="Heizkreis 1 nach Programm steuern",
                           options={0: "Aus", 1: "Ein"}),
    0x0307: KnownParameter(en="Heating circuit 2 active",
                           de="Heizkreis 2 nach Programm steuern",
                           options={0: "Aus", 1: "Ein"}),
    0x0308: KnownParameter(en="Heating circuit 3 active",
                           de="Heizkreis 3 nach Programm steuern",
                           options={0: "Aus", 1: "Ein"}),
    0x0309: KnownParameter(en="Heating circuit 4 active",
                           de="Heizkreis 4 nach Programm steuern",
                           options={0: "Aus", 1: "Ein"}),

    # --- Circulation ---
    0x02F0: KnownParameter(en="Circulation pump off at return temp",
                           de="Zirkulation Pumpe aus bei RL Temperatur"),
    0x02F1: KnownParameter(en="Circulation pump overrun time",
                           de="Nachlauf der Zirkulations Pumpe"),
}


def is_basic_param(address: int) -> bool:
    """Return True if the address is a known basic parameter."""
    return address in KNOWN_BASIC_PARAMS


def get_known_param(address: int) -> KnownParameter | None:
    """Look up metadata for a known parameter address."""
    return KNOWN_BASIC_PARAMS.get(address)


def get_option_labels(address: int) -> dict[int, str] | None:
    """Get option labels for a known parameter (for select entities)."""
    param = KNOWN_BASIC_PARAMS.get(address)
    if param and param.options:
        return param.options
    return None
