"""Tests for the seasonal masthead: meteorological seasons, whole months."""

from __future__ import annotations

from datetime import date

import pytest

from weather_bureau_light.season import season_for


@pytest.mark.parametrize(
    "month,expected",
    [
        (12, "winter"), (1, "winter"), (2, "winter"),
        (3, "spring"), (4, "spring"), (5, "spring"),
        (6, "summer"), (7, "summer"), (8, "summer"),
        (9, "autumn"), (10, "autumn"), (11, "autumn"),
    ],
)
def test_every_month_has_its_season(month, expected):
    assert season_for(date(2026, month, 1)) == expected


def test_season_turns_on_the_first_not_the_solstice():
    assert season_for(date(2026, 11, 30)) == "autumn"
    assert season_for(date(2026, 12, 1)) == "winter"


def test_december_belongs_with_the_following_january():
    assert season_for(date(2026, 12, 31)) == season_for(date(2027, 1, 1))
