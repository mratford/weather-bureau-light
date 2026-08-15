"""Which season the page dresses for.

Meteorological seasons - whole months - rather than the astronomical ones that turn
on a solstice or equinox, so the masthead changes on the 1st and never mid-week.
"""

from __future__ import annotations

from datetime import date

#: Indexed by (month % 12) // 3, which puts December with the following January.
_SEASONS = ("winter", "spring", "summer", "autumn")


#: Dates that dress the masthead themselves, whatever season they fall in.
_HOLIDAYS = {(12, 24): "christmas", (12, 25): "christmas", (12, 26): "christmas"}


def season_for(day: date) -> str:
    """December to February winter, March to May spring, and so on."""
    return _SEASONS[(day.month % 12) // 3]


def palette_for(day: date) -> str:
    """Which masthead palette the page wears, which is the season unless a holiday
    claims the day. The name is used as a CSS class, so it has to match a season-*
    rule in the stylesheet."""
    return _HOLIDAYS.get((day.month, day.day)) or season_for(day)
