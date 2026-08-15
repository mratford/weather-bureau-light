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
    )
    # 02:00Z precedes the percentile data, so no column should exist for it.
    stamps = {s.time.astimezone(timezone.utc).isoformat() for d in forecast.days for s in d.timesteps}
    assert "2026-08-15T02:00:00+00:00" not in stamps
    # And every rendered column has a real temperature.
    assert all(
        s.median("temperature") is not None for d in forecast.days for s in d.timesteps
    )
