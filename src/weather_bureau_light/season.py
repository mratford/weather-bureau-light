"""Choose the season used by the page.

The page uses meteorological seasons, which begin on the first day of a month rather
than at an astronomical event.
"""

from __future__ import annotations

from datetime import date

#: Indexed by (month % 12) // 3, which groups December with January and February.
_SEASONS = ("winter", "spring", "summer", "autumn")


#: Dates with a holiday-specific palette, regardless of their season.
_HOLIDAYS = {
    (10, 31): "halloween",
    (12, 24): "christmas",
    (12, 25): "christmas",
    (12, 26): "christmas",
}


def season_for(day: date) -> str:
    """Return winter for December-February, spring for March-May, and so on."""
    return _SEASONS[(day.month % 12) // 3]


def palette_for(day: date) -> str:
    """Return the masthead palette, using a holiday override when applicable."""
    return _HOLIDAYS.get((day.month, day.day)) or season_for(day)
