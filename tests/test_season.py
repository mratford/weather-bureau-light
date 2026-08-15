"""Tests for the seasonal masthead: meteorological seasons, whole months."""

from __future__ import annotations

from datetime import date

import pytest

from weather_bureau_light.season import palette_for, season_for


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


@pytest.mark.parametrize("day", [24, 25, 26])
def test_christmas_claims_its_three_days(day):
    assert palette_for(date(2026, 12, day)) == "christmas"


@pytest.mark.parametrize("day", [23, 27, 31])
def test_the_rest_of_december_stays_wintry(day):
    assert palette_for(date(2026, 12, day)) == "winter"


def test_halloween_claims_the_31st_of_october():
    assert palette_for(date(2026, 10, 31)) == "halloween"


@pytest.mark.parametrize("month,day", [(10, 30), (11, 1)])
def test_the_days_either_side_of_halloween_are_autumn(month, day):
    assert palette_for(date(2026, month, day)) == "autumn"


def test_a_holiday_does_not_disturb_the_season_itself():
    """season_for stays the meteorological answer; only the palette changes."""
    assert season_for(date(2026, 12, 25)) == "winter"


def test_palette_is_the_season_the_rest_of_the_year():
    for month in range(1, 13):
        day = date(2026, month, 15)
        assert palette_for(day) == season_for(day)
