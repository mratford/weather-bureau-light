"""Met Office significant weather codes (0-30) and their presentation.

The Met Office's symbol artwork is Crown copyright, so each code maps to a label and
an id in this project's SVG sprite.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Symbol:
    code: int
    label: str
    sprite: str
    night: bool = False

    @property
    def known(self) -> bool:
        """Return false for codes beyond the weather-code data.

        Symbol parameters end around day eight while temperature continues to day
        fourteen, so later forecast entries may have no symbol.
        """
        return self.sprite != "unknown"


# Codes from the Met Office definitions. Day/night pairs share a sprite whose artwork
# differs only in the sun or moon element.
_SYMBOLS: dict[int, Symbol] = {
    -1: Symbol(-1, "Trace rain", "rain-light"),
    0: Symbol(0, "Clear night", "clear", night=True),
    1: Symbol(1, "Sunny day", "clear"),
    2: Symbol(2, "Partly cloudy", "partly-cloudy", night=True),
    3: Symbol(3, "Sunny intervals", "partly-cloudy"),
    4: Symbol(4, "Not used", "unknown"),
    5: Symbol(5, "Mist", "mist"),
    6: Symbol(6, "Fog", "fog"),
    7: Symbol(7, "Cloudy", "cloudy"),
    8: Symbol(8, "Overcast", "overcast"),
    9: Symbol(9, "Light rain shower", "shower-light", night=True),
    10: Symbol(10, "Light rain shower", "shower-light"),
    11: Symbol(11, "Drizzle", "drizzle"),
    12: Symbol(12, "Light rain", "rain-light"),
    13: Symbol(13, "Heavy rain shower", "shower-heavy", night=True),
    14: Symbol(14, "Heavy rain shower", "shower-heavy"),
    15: Symbol(15, "Heavy rain", "rain-heavy"),
    16: Symbol(16, "Sleet shower", "sleet", night=True),
    17: Symbol(17, "Sleet shower", "sleet"),
    18: Symbol(18, "Sleet", "sleet"),
    19: Symbol(19, "Hail shower", "hail", night=True),
    20: Symbol(20, "Hail shower", "hail"),
    21: Symbol(21, "Hail", "hail"),
    22: Symbol(22, "Light snow shower", "snow-light", night=True),
    23: Symbol(23, "Light snow shower", "snow-light"),
    24: Symbol(24, "Light snow", "snow-light"),
    25: Symbol(25, "Heavy snow shower", "snow-heavy", night=True),
    26: Symbol(26, "Heavy snow shower", "snow-heavy"),
    27: Symbol(27, "Heavy snow", "snow-heavy"),
    28: Symbol(28, "Thunder shower", "thunder", night=True),
    29: Symbol(29, "Thunder shower", "thunder"),
    30: Symbol(30, "Thunder", "thunder"),
}

UNKNOWN = Symbol(-99, "Not available", "unknown")


def lookup(code: float | int | None) -> Symbol:
    if code is None:
        return UNKNOWN
    try:
        return _SYMBOLS.get(int(code), UNKNOWN)
    except (TypeError, ValueError):
        return UNKNOWN
