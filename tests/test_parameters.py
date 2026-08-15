"""Tests for parameter-name resolution.

Exact BPF parameter names are not published, so these cover both naming conventions
the service is known to mix (camelCase in CoverageJSON, snake_case CF names).
"""

from __future__ import annotations

from weather_bureau_light import parameters
from weather_bureau_light.parameters import PERCENTILE_FIELDS, PROBABILITY_FIELDS, resolve

CAMEL = [
    "airTemperature",
    "feelsLikeTemperature",
    "windSpeed10m",
    "windGustSpeed10m",
    "windDirectionFrom10m",
    "screenRelativeHumidity",
    "visibility",
    "pressureAtMeanSeaLevel",
    "uvIndex",
    "weatherSymbol",
]

SNAKE = [
    "air_temperature",
    "feels_like_temperature",
    "wind_speed_at_10m",
    "wind_gust_at_10m",
    "wind_from_direction_at_10m",
    "relative_humidity_at_1_5m",
    "visibility_at_1_5m",
    "pressure_at_mean_sea_level",
    "uv_index",
    "weather_symbol",
]


def test_resolves_camel_case_names():
    result = resolve(CAMEL, PERCENTILE_FIELDS)
    assert result.missing == []
    assert result.mapping["temperature"] == "airTemperature"
    assert result.mapping["feels_like"] == "feelsLikeTemperature"
    assert result.mapping["wind_speed"] == "windSpeed10m"
    assert result.mapping["wind_gust"] == "windGustSpeed10m"
    assert result.mapping["pressure"] == "pressureAtMeanSeaLevel"
    assert result.mapping["weather_code"] == "weatherSymbol"


def test_resolves_snake_case_names():
    result = resolve(SNAKE, PERCENTILE_FIELDS)
    assert result.missing == []
    assert result.mapping["temperature"] == "air_temperature"
    assert result.mapping["humidity"] == "relative_humidity_at_1_5m"
    assert result.mapping["visibility"] == "visibility_at_1_5m"


def test_temperature_and_feels_like_do_not_collide():
    """Both match 'temperature'; each must claim a distinct parameter."""
    result = resolve(CAMEL, PERCENTILE_FIELDS)
    assert result.mapping["temperature"] != result.mapping["feels_like"]


def test_wind_speed_does_not_steal_the_gust_parameter():
    result = resolve(CAMEL, PERCENTILE_FIELDS)
    assert result.mapping["wind_speed"] == "windSpeed10m"
    assert result.mapping["wind_gust"] == "windGustSpeed10m"


def test_missing_parameters_are_reported_not_silently_dropped():
    result = resolve(["airTemperature"], PERCENTILE_FIELDS)
    assert result.mapping["temperature"] == "airTemperature"
    assert "uv" in result.missing
    assert "weather_code" in result.missing


def test_probability_field_matches_threshold_parameter():
    available = [
        "probabilityOfPrecipitationRateAboveThreshold",
        "probabilityOfSnowfallRateAboveThreshold",
    ]
    result = resolve(available, PROBABILITY_FIELDS)
    assert result.mapping["precipitation_probability"] == "probabilityOfPrecipitationRateAboveThreshold"


def test_normalise_collapses_conventions():
    assert parameters._normalise("airTemperature") == "air_temperature"
    assert parameters._normalise("air_temperature") == "air_temperature"
    assert parameters._normalise("windSpeed10m") == "wind_speed10m"
    assert parameters._normalise("UVIndex") == "uvindex"


def test_field_by_key():
    assert parameters.field_by_key("temperature").label == "Temperature"
    assert parameters.field_by_key("precipitation_probability").unit == "%"
    assert parameters.field_by_key("nope") is None


def test_weather_code_is_not_probabilistic():
    """The symbol is deterministic even inside the percentiles collection."""
    assert parameters.field_by_key("weather_code").probabilistic is False
    assert parameters.field_by_key("temperature").probabilistic is True
