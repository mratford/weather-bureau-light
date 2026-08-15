"""Synthetic BPF responses matching the live API shape.

Structure confirmed against the real service by scripts/discover.py:
a CoverageCollection with one coverage per parameter, each carrying axes
locationId / percentiles / t / x / y / z, ranges declared percentile-major
(axisNames ["percentiles", "t"]), percentile axis values as strings, and SI units.
Probability parameters replace the percentile axis with a threshold axis whose
values are strings like ">2.7777778E-8".
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

PERCENTILES = ["5", "10", "25", "50", "75", "90", "95"]
START = datetime(2026, 8, 15, 3, 0, tzinfo=timezone.utc)
N_HOURS = 48
SITE_ID = "00350584"
LON, LAT, ALT = 0.3088, 51.62, 104.0

LOCATIONS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": SITE_ID,
            "geometry": {"type": "Point", "coordinates": [LON, LAT, ALT]},
            "properties": {},
        },
        {
            "type": "Feature",
            "id": "00000003",
            "geometry": {"type": "Point", "coordinates": [-0.1276, 51.5072, 11.0]},
            "properties": {},
        },
        {
            "type": "Feature",
            "id": "00000009",
            "geometry": {"type": "Point", "coordinates": [-7.3093, 54.9966, 40.0]},
            "properties": {},
        },
    ],
}

PERCENTILE_PARAMS = {
    "airPressureAtSeaLevel": "Pa",
    "airTemperature1p5m": "K",
    "airTemperature1p5mMaximumPt12h": "K",
    "airTemperature1p5mMinimumPt12h": "K",
    "feelsLikeTemperature1p5m": "K",
    "relativeHumidity1p5m": "%",
    "ultravioletIndex": "1",
    "ultravioletIndexMaximumPt24h": "1",
    "visibilityInAir1p5m": "m",
    "visibilityInAirInVicinity1p5m10000m": "m",
    "weatherCodePt01h": "1",
    "weatherCodePt03h": "1",
    "windFromDirection10mMean": "degrees",
    "windSpeed10m": "m s-1",
    "windSpeedOfGust10mMaximumPt01h": "m s-1",
}

PROBABILITY_PARAM = "probabilityOfLwePrecipitationRateAboveThreshold"
# Real threshold labels span many orders of magnitude; 2.7777778E-8 m/s is 0.1 mm/hr.
THRESHOLDS = [
    ">0.0",
    ">8.333333E-9",
    ">2.7777778E-8",
    ">6.944445E-8",
    ">1.388889E-7",
    ">2.777778E-7",
    ">1.388889E-6",
]

# Deterministic parameters carry no percentile axis.
DETERMINISTIC = {"weatherCodePt01h", "weatherCodePt03h"}


def _times(n: int = N_HOURS, step_hours: int = 1) -> list[str]:
    return [
        (START + timedelta(hours=i * step_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for i in range(n)
    ]


def _diurnal(hour: int) -> float:
    """A plausible daily temperature curve, coldest around 04:00."""
    return 15.0 + 6.0 * math.sin((hour - 9) / 24 * 2 * math.pi)


def _base_value(param: str, hour: int) -> float:
    curve = _diurnal(hour % 24)
    return {
        "airPressureAtSeaLevel": 101800.0 + 20 * hour,
        "airTemperature1p5m": 273.15 + curve,
        "airTemperature1p5mMaximumPt12h": 273.15 + curve + 3,
        "airTemperature1p5mMinimumPt12h": 273.15 + curve - 3,
        "feelsLikeTemperature1p5m": 273.15 + curve - 1.5,
        "relativeHumidity1p5m": 70.0 - curve,
        "ultravioletIndex": max(0.0, 5.0 * math.sin((hour % 24 - 6) / 12 * math.pi)),
        "ultravioletIndexMaximumPt24h": 5.0,
        "visibilityInAir1p5m": 25000.0 + 200 * hour,
        "visibilityInAirInVicinity1p5m10000m": 9000.0,
        "weatherCodePt01h": float([1, 3, 7, 12][hour % 4]),
        "weatherCodePt03h": float([1, 3, 7, 12][hour % 4]),
        "windFromDirection10mMean": (200 + hour * 3) % 360,
        "windSpeed10m": 4.0 + 2.0 * math.sin(hour / 6),
        "windSpeedOfGust10mMaximumPt01h": 9.0 + 4.0 * math.sin(hour / 6),
    }[param]


def _spread(param: str) -> float:
    """How far the outer percentiles sit from the median."""
    return {
        "airTemperature1p5m": 2.0,
        "feelsLikeTemperature1p5m": 2.2,
        "windSpeed10m": 1.5,
        "windSpeedOfGust10mMaximumPt01h": 3.0,
    }.get(param, 0.5)


def _coverage(param: str, unit: str, times: list[str]) -> dict:
    domain_axes = {
        "locationId": {"values": [SITE_ID]},
        "t": {"values": times},
        "x": {"values": [LON]},
        "y": {"values": [LAT]},
        "z": {"values": [ALT]},
    }

    if param in DETERMINISTIC:
        axis_names = ["t"]
        shape = [len(times)]
        values = [_base_value(param, h) for h in range(len(times))]
    else:
        domain_axes["percentiles"] = {"values": PERCENTILES}
        # Declared percentile-major, as the live service does.
        axis_names = ["percentiles", "t"]
        shape = [len(PERCENTILES), len(times)]
        spread = _spread(param)
        values = [
            _base_value(param, h) + spread * (float(p) - 50.0) / 45.0
            for p in PERCENTILES
            for h in range(len(times))
        ]

    return {
        "type": "Coverage",
        "id": param,
        "parameters": {
            param: {
                "type": "Parameter",
                "observedProperty": {"label": {"en": param}},
                "unit": {"symbol": unit},
            }
        },
        "domain": {"type": "Domain", "axes": domain_axes},
        "ranges": {
            param: {
                "type": "NdArray",
                "dataType": "float",
                "axisNames": axis_names,
                "shape": shape,
                "values": values,
            }
        },
    }


def build_percentile_doc(parameter_names: list[str] | None = None) -> dict:
    times = _times()
    wanted = parameter_names or list(PERCENTILE_PARAMS)
    return {
        "type": "CoverageCollection",
        "domainType": "PointSeries",
        "referencing": [],
        "coverages": [
            _coverage(p, PERCENTILE_PARAMS[p], times) for p in wanted if p in PERCENTILE_PARAMS
        ],
    }


def build_probability_doc(parameter_names: list[str] | None = None) -> dict:
    times = _times()
    axis = f"{PROBABILITY_PARAM}Values"
    # Probability falls as the threshold rises, and units are a 0-1 fraction.
    values = [
        round(max(0.0, abs(math.sin(h / 5)) * 0.9 - 0.12 * ti), 3)
        for ti in range(len(THRESHOLDS))
        for h in range(len(times))
    ]
    return {
        "type": "CoverageCollection",
        "domainType": "PointSeries",
        "coverages": [
            {
                "type": "Coverage",
                "id": PROBABILITY_PARAM,
                "parameters": {
                    PROBABILITY_PARAM: {
                        "type": "Parameter",
                        "observedProperty": {"label": {"en": PROBABILITY_PARAM}},
                        "unit": {"symbol": "1"},
                    }
                },
                "domain": {
                    "type": "Domain",
                    "axes": {
                        "locationId": {"values": [SITE_ID]},
                        axis: {"values": THRESHOLDS},
                        "t": {"values": times},
                        "x": {"values": [LON]},
                        "y": {"values": [LAT]},
                        "z": {"values": [ALT]},
                    },
                },
                "ranges": {
                    PROBABILITY_PARAM: {
                        "type": "NdArray",
                        "dataType": "float",
                        "axisNames": [axis, "t"],
                        "shape": [len(THRESHOLDS), len(times)],
                        "values": values,
                    }
                },
            }
        ],
    }


INSTANCES = {
    "links": [],
    "instances": [
        {
            "id": "blended",
            "extent": {"temporal": {"interval": [[_times()[0], _times()[-1]]]}},
            "parameter_names": {k: {"unit": {"symbol": v}} for k, v in PERCENTILE_PARAMS.items()},
        }
    ],
}

COLLECTIONS = {
    "collections": [
        {
            "id": "uk-spot-percentiles",
            "parameter_names": {
                k: {"unit": {"symbol": v}} for k, v in PERCENTILE_PARAMS.items()
            },
        },
        {
            "id": "uk-spot-probabilities",
            "parameter_names": {PROBABILITY_PARAM: {"unit": {"symbol": "1"}}},
        },
    ]
}

# postcodes.io responses.
PLACES_RESPONSE = {
    "status": 200,
    "result": [
        {
            "name_1": "Brentwood",
            "county_unitary": "Essex",
            "local_type": "Town",
            "latitude": 51.6214,
            "longitude": 0.3053,
        },
        {
            "name_1": "Brentwood Park",
            "county_unitary": "Essex",
            "local_type": "Suburban Area",
            "latitude": 51.57,
            "longitude": 0.47,
        },
        {
            "name_1": "London",
            "county_unitary": "Greater London",
            "local_type": "City",
            "latitude": 51.5072,
            "longitude": -0.1276,
        },
        {
            "name_1": "Londonderry",
            "county_unitary": "County Londonderry",
            "local_type": "City",
            "latitude": 54.9966,
            "longitude": -7.3093,
        },
    ],
}

POSTCODE_RESPONSE = {
    "status": 200,
    "result": {
        "postcode": "CM14 4BX",
        "latitude": 51.6198,
        "longitude": 0.3061,
        "admin_district": "Brentwood",
        "region": "East of England",
    },
}

REVERSE_RESPONSE = {
    "status": 200,
    "result": [
        {
            "postcode": "CM15 8AA",
            "latitude": LAT,
            "longitude": LON,
            "admin_ward": "Brentwood North",
            "admin_district": "Brentwood",
            "region": "East of England",
        }
    ],
}
