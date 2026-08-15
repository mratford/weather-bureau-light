"""Which season the page dresses for.

Meteorological seasons - whole months - rather than the astronomical ones that turn
on a solstice or equinox, so the masthead changes on the 1st and never mid-week.
"""

from __future__ import annotations

from datetime import date

#: Indexed by (month % 12) // 3, which puts December with the following January.
_SEASONS = ("winter", "spring", "summer", "autumn")


def season_for(day: date) -> str:
    """December to February winter, March to May spring, and so on."""
    return _SEASONS[(day.month % 12) // 3]
