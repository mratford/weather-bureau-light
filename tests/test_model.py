"""Tests for forecast assembly: merging the two collections, units, day grouping."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from weather_bureau_light import covjson, model, parameters
from weather_bureau_light.parameters import PERCENTILE_FIELDS, PROBABILITY_FIELDS
from weather_bureau_light.sites import Site

from bpf_fixtures import (
    PERCENTILE_PARAMS,
    PROBABILITY_PARAM,
    build_percentile_doc,
    build_probability_doc,
)

UK = ZoneInfo("Europe/London")
SITE = Site("00350584", 51.62, 0.3088, "Brentwood", "Essex")

#: Elapsed hours are dropped from the table, so these tests state where in the day they
#: are standing. This is the first timestep the fixtures publish, which keeps every
#: hour they build in the future and the assertions independent of the real clock.
NOW = datetime(2026, 8, 15, 3, 0, tzinfo=timezone.utc)


@pytest.fixture
def forecast() -> model.Forecast:
    percentiles = covjson.parse_collection(build_percentile_doc())
    probabilities = covjson.parse_collection(build_probability_doc())
    return model.build(
        site=SITE,
        percentiles=percentiles,
        percentile_resolution=parameters.resolve(PERCENTILE_PARAMS, PERCENTILE_FIELDS),
        probabilities=probabilities,
        probability_resolution=parameters.resolve([PROBABILITY_PARAM], PROBABILITY_FIELDS),
        tz=UK,
        now=NOW,
    )


def test_all_fields_resolve(forecast):
    assert forecast.missing_fields == []


def test_groups_48_hours_into_days(forecast):
    # 48 UTC hours starting midnight, shown in BST, spans three local days.
    assert len(forecast.days) == 3
    assert forecast.days[0].date == date(2026, 8, 15)


def _long_forecast(hours: int, **kwargs) -> model.Forecast:
    return model.build(
        site=SITE,
        percentiles=covjson.parse_collection(build_percentile_doc(hours=hours)),
        percentile_resolution=parameters.resolve(PERCENTILE_PARAMS, PERCENTILE_FIELDS),
        tz=UK,
        now=NOW,
        **kwargs,
    )


def test_long_forecast_is_capped_at_seven_days():
    """The API runs to about fourteen days; the strip shows the first seven."""
    forecast = _long_forecast(14 * 24)
    assert len(forecast.days) == model.MAX_DAYS == 7
    assert forecast.days[0].date == date(2026, 8, 15)
    assert forecast.days[-1].date == date(2026, 8, 21)


def test_cap_does_not_pad_a_short_forecast():
    assert len(_long_forecast(48).days) == 3


def test_day_cap_is_overridable():
    assert len(_long_forecast(14 * 24, max_days=3).days) == 3


def _doc(*coverages) -> dict:
    return {"type": "CoverageCollection", "domainType": "PointSeries", "coverages": list(coverages)}


def _mixed_resolution_forecast(with_symbols: bool = True) -> model.Forecast:
    """Temperature every hour, weather symbol only every third - the shape the API
    takes in the changeover around day five."""
    from bpf_fixtures import _coverage, _times

    coverages = [_coverage("airTemperature1p5m", "K", _times(12, 1))]
    if with_symbols:
        coverages.append(_coverage("weatherCodePt03h", "1", _times(4, 3)))

    names = [c["id"] for c in coverages]
    return model.build(
        site=SITE,
        percentiles=covjson.parse_collection(_doc(*coverages)),
        percentile_resolution=parameters.resolve(names, PERCENTILE_FIELDS),
        tz=UK,
        now=NOW,
    )


def test_hours_without_a_weather_symbol_are_dropped():
    forecast = _mixed_resolution_forecast()
    steps = [s for d in forecast.days for s in d.timesteps]
    assert steps, "everything was dropped"
    assert all(s.median("weather_code") is not None for s in steps)
    # Twelve hourly columns, a symbol on every third: four survive.
    assert len(steps) == 4


def test_dropped_hours_still_count_towards_the_day_high_and_low():
    """Hiding a column must not move the figures on the day tab."""
    forecast = _mixed_resolution_forecast()
    day = forecast.days[0]
    shown = [t.median("temperature") for t in day.timesteps]
    hidden = [t.median("temperature") for t in day.all_timesteps]
    assert len(hidden) > len(shown)
    assert day.max_temp == int(round(max(v for v in hidden if v is not None)))
    assert day.min_temp == int(round(min(v for v in hidden if v is not None)))


def test_a_day_with_no_symbols_at_all_keeps_its_hours():
    """A missing parameter is not the same as the reporting thinning out; an empty
    table would be worse than a table with no symbol row."""
    forecast = _mixed_resolution_forecast(with_symbols=False)
    assert sum(len(d.timesteps) for d in forecast.days) == 12


def test_temperature_converted_to_celsius(forecast):
    temps = [
        s.median("temperature") for d in forecast.days for s in d.timesteps
    ]
    assert all(-10 < t < 40 for t in temps), "values still look like Kelvin"


def test_pressure_converted_to_hpa(forecast):
    pressure = forecast.days[0].timesteps[0].median("pressure")
    assert 950 < pressure < 1060


def test_wind_converted_to_mph(forecast):
    step = forecast.days[0].timesteps[0]
    # 4 m/s base becomes about 9 mph.
    assert 2 < step.median("wind_speed") < 40


def test_median_sits_between_the_percentile_bounds(forecast):
    value = forecast.days[0].timesteps[6].value("temperature")
    assert value.lower < value.median < value.upper


def test_range_row_renders_text(forecast):
    value = forecast.days[0].timesteps[6].value("temperature")
    assert value.has_range
    assert " to " in value.range_text("°")


def test_deterministic_field_has_no_range(forecast):
    """The weather symbol carries no percentile axis, so no spread."""
    value = forecast.days[0].timesteps[0].value("weather_code")
    assert value.lower is None and value.upper is None


def test_probability_row_merged_from_second_collection(forecast):
    values = [
        s.median("precipitation_probability")
        for d in forecast.days
        for s in d.timesteps
    ]
    assert any(v is not None for v in values)
    assert all(0 <= v <= 100 for v in values if v is not None)


def test_probability_absent_when_collection_missing():
    percentiles = covjson.parse_collection(build_percentile_doc())
    forecast = model.build(
        site=SITE,
        percentiles=percentiles,
        percentile_resolution=parameters.resolve(PERCENTILE_PARAMS, PERCENTILE_FIELDS),
        tz=UK,
        now=NOW,
    )
    step = forecast.days[0].timesteps[0]
    assert step.median("precipitation_probability") is None
    assert step.median("temperature") is not None


def test_day_max_and_min(forecast):
    day = forecast.days[0]
    assert day.max_temp >= day.min_temp


def test_sunrise_and_sunset_populated(forecast):
    day = forecast.days[0]
    assert day.sunrise.date() == day.date
    assert day.sunset > day.sunrise


def test_daylight_flag_tracks_sun_times(forecast):
    for day in forecast.days:
        for step in day.timesteps:
            if step.time.hour == 13:
                assert step.is_daylight
            if step.time.hour == 2:
                assert not step.is_daylight


def test_symbol_lookup_from_code(forecast):
    labels = {s.symbol.label for d in forecast.days for s in d.timesteps}
    assert "Sunny day" in labels or "Cloudy" in labels


def test_night_symbol_swapped_to_day_when_sun_is_up():
    """A day/night pair reported as the night variant during daylight flips to day."""
    step = model.Timestep(
        time=datetime(2026, 8, 15, 13, 0, tzinfo=UK),
        values={"weather_code": model.Value(median=0)},  # 0 = Clear night
        is_daylight=True,
    )
    assert step.symbol.night is False


def test_times_are_local(forecast):
    step = forecast.days[0].timesteps[0]
    assert step.time.tzinfo is not None
    assert step.time.utcoffset().total_seconds() == 3600  # BST in August


def test_day_selection_by_iso(forecast):
    second = forecast.days[1]
    assert forecast.day(second.iso).date == second.date
    assert forecast.day(None).date == forecast.days[0].date
    # An unknown date falls back to the first day rather than erroring.
    assert forecast.day("1999-01-01").date == forecast.days[0].date


def test_strong_gust_flag():
    step = model.Timestep(
        time=datetime(2026, 8, 15, 12, 0, tzinfo=UK),
        values={"wind_gust": model.Value(median=35)},
    )
    assert step.gust_is_strong
    calm = model.Timestep(
        time=datetime(2026, 8, 15, 12, 0, tzinfo=UK),
        values={"wind_gust": model.Value(median=10)},
    )
    assert not calm.gust_is_strong


def test_visibility_and_uv_bands(forecast):
    step = forecast.days[0].timesteps[0]
    assert step.visibility_band.code in {"VP", "P", "M", "G", "VG", "E"}


def test_missing_value_renders_as_none():
    step = model.Timestep(time=datetime(2026, 8, 15, 12, 0, tzinfo=UK))
    assert step.rounded("temperature") is None
    assert step.value("temperature").range_text() == "—"


def test_day_grouping_across_dst_boundary():
    """The clocks go back on 25 Oct 2026, making that local day 25 hours long."""
    from datetime import timedelta

    start = datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc)
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(48)]
    doc = {
        "domain": {"axes": {"x": {"values": [0.3]}, "y": {"values": [51.6]}, "t": {"values": times}}},
        "parameters": {},
        "ranges": {
            "airTemperature1p5m": {
                "axisNames": ["t"],
                "shape": [48],
                "values": [283.15] * 48,
            }
        },
    }
    forecast = model.build(
        site=SITE,
        percentiles=covjson.parse_collection(doc),
        percentile_resolution=parameters.resolve(["airTemperature1p5m"], PERCENTILE_FIELDS),
        tz=UK,
        now=NOW,
    )
    by_date = {d.date: len(d.timesteps) for d in forecast.days}
    # 25 Oct starts at 00:00 UTC = 01:00 BST, so 24 UTC hours land inside it,
    # covering local 01:00 through 24:00 without spilling into the 26th.
    assert by_date[date(2026, 10, 25)] == 24
    assert sum(by_date.values()) == 48


def test_edge_timesteps_without_percentile_data_are_dropped():
    """The two collections start an hour apart, leaving a lone probability column."""
    from bpf_fixtures import PROBABILITY_PARAM, THRESHOLDS, build_percentile_doc

    prob_times = ["2026-08-15T02:00:00Z"] + [
        f"2026-08-15T{h:02d}:00:00Z" for h in range(3, 8)
    ]
    axis = f"{PROBABILITY_PARAM}Values"
    prob_doc = {
        "type": "CoverageCollection",
        "coverages": [
            {
                "parameters": {PROBABILITY_PARAM: {"unit": {"symbol": "1"}}},
                "domain": {
                    "axes": {
                        axis: {"values": THRESHOLDS},
                        "t": {"values": prob_times},
                    }
                },
                "ranges": {
                    PROBABILITY_PARAM: {
                        "axisNames": [axis, "t"],
                        "shape": [len(THRESHOLDS), len(prob_times)],
                        "values": [
                            max(0.0, 0.5 - 0.05 * k)
                            for k in range(len(THRESHOLDS))
                            for _ in prob_times
                        ],
                    }
                },
            }
        ],
    }

    forecast = model.build(
        site=SITE,
        percentiles=covjson.parse_collection(build_percentile_doc()),
        percentile_resolution=parameters.resolve(PERCENTILE_PARAMS, PERCENTILE_FIELDS),
        probabilities=covjson.parse_collection(prob_doc),
        probability_resolution=parameters.resolve([PROBABILITY_PARAM], PROBABILITY_FIELDS),
        tz=UK,
        now=NOW,
    )
    # 02:00Z precedes the percentile data, so no column should exist for it.
    stamps = {s.time.astimezone(timezone.utc).isoformat() for d in forecast.days for s in d.timesteps}
    assert "2026-08-15T02:00:00+00:00" not in stamps
    # And every rendered column has a real temperature.
    assert all(
        s.median("temperature") is not None for d in forecast.days for s in d.timesteps
    )


# --- Age wording ----------------------------------------------------------------


def _aged(hours: float):
    """A Forecast whose data was retrieved a given number of hours ago."""
    from datetime import datetime, timedelta

    from weather_bureau_light.config import UK_TZ
    from weather_bureau_light.model import Forecast

    issued = datetime.now(UK_TZ) - timedelta(hours=hours)
    return Forecast(site=None, days=[], issued=issued, stale=True)


def test_age_text_reads_in_whole_units():
    assert _aged(0).age_text == "just now"
    assert _aged(1 / 60 * 20).age_text == "20 minutes ago"
    assert _aged(3).age_text == "3 hours ago"
    assert _aged(50).age_text == "2 days ago"


def test_age_text_counts_in_hours_past_a_day():
    """Days only take over at 36 hours: 'yesterday afternoon' is still worth saying
    precisely when someone is deciding whether to trust the numbers."""
    assert _aged(30).age_text == "30 hours ago"


def test_age_text_is_singular_where_it_should_be():
    """Anything under 90 seconds is 'just now', so an hour is the first singular."""
    assert _aged(1).age_text == "1 hour ago"
    assert _aged(1 / 60).age_text == "just now"


def test_age_text_survives_an_unknown_issue_time():
    from weather_bureau_light.model import Forecast

    assert Forecast(site=None, days=[], issued=None).age_text == "an unknown time ago"


# --- Elapsed hours --------------------------------------------------------------


def _day_forecast(now: datetime) -> model.Forecast:
    """A full 48 hours of fixture data, read at a given moment."""
    return model.build(
        site=SITE,
        percentiles=covjson.parse_collection(build_percentile_doc()),
        percentile_resolution=parameters.resolve(PERCENTILE_PARAMS, PERCENTILE_FIELDS),
        tz=UK,
        now=now,
    )


def test_the_current_hour_is_still_shown_partway_through_it():
    """At 17:49 the 17:00 row describes the hour being lived through."""
    forecast = _day_forecast(datetime(2026, 8, 15, 17, 49, tzinfo=UK))
    today = forecast.days[0]
    assert today.date == date(2026, 8, 15)
    assert today.timesteps[0].time.hour == 17


def test_hours_already_past_are_dropped():
    forecast = _day_forecast(datetime(2026, 8, 15, 17, 49, tzinfo=UK))
    hours = [t.time.hour for t in forecast.days[0].timesteps]
    assert 16 not in hours and 9 not in hours
    assert hours == sorted(hours)


def test_the_hour_drops_off_the_moment_the_clock_turns():
    forecast = _day_forecast(datetime(2026, 8, 15, 18, 0, tzinfo=UK))
    assert forecast.days[0].timesteps[0].time.hour == 18


def test_later_days_keep_all_their_hours():
    """Only today has hours behind it; tomorrow must not be trimmed."""
    late = _day_forecast(datetime(2026, 8, 15, 22, 0, tzinfo=UK))
    early = _day_forecast(datetime(2026, 8, 15, 4, 0, tzinfo=UK))
    assert len(late.days[1].timesteps) == len(early.days[1].timesteps)


def test_elapsed_hours_do_not_move_the_day_high_and_low():
    """The afternoon's peak still belongs to today after the afternoon has gone."""
    morning = _day_forecast(datetime(2026, 8, 15, 4, 0, tzinfo=UK))
    evening = _day_forecast(datetime(2026, 8, 15, 21, 0, tzinfo=UK))
    assert evening.days[0].max_temp == morning.days[0].max_temp
    assert evening.days[0].min_temp == morning.days[0].min_temp


def test_elapsed_hours_do_not_change_the_day_tab_symbol():
    morning = _day_forecast(datetime(2026, 8, 15, 4, 0, tzinfo=UK))
    evening = _day_forecast(datetime(2026, 8, 15, 21, 0, tzinfo=UK))
    assert evening.days[0].symbol.label == morning.days[0].symbol.label


def test_a_wholly_elapsed_day_is_dropped_rather_than_shown_empty():
    """Reachable when the data being served is old enough to have run out."""
    forecast = _day_forecast(datetime(2026, 8, 16, 6, 0, tzinfo=UK))
    assert forecast.days[0].date == date(2026, 8, 16)
    assert all(d.timesteps for d in forecast.days)


def test_defaults_to_the_real_clock_when_no_moment_is_given(monkeypatch):
    """The application does not pass one; the seam exists for these tests."""
    monkeypatch.setattr(
        model, "_now", lambda tz: datetime(2026, 8, 15, 17, 49, tzinfo=UK)
    )
    forecast = model.build(
        site=SITE,
        percentiles=covjson.parse_collection(build_percentile_doc()),
        percentile_resolution=parameters.resolve(PERCENTILE_PARAMS, PERCENTILE_FIELDS),
        tz=UK,
    )
    assert forecast.days[0].timesteps[0].time.hour == 17
