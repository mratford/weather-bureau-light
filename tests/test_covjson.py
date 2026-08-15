"""Tests for the CoverageJSON parser, focused on N-d axis reshaping.

A wrong axis order here produces plausible numbers rather than an error, so these
tests use values that encode their own coordinates: value = t*100 + p.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weather_bureau_light import covjson


def build_doc(times: list[str], percentiles: list[float]) -> dict:
    """Row-major values where each entry encodes (time index, percentile index)."""
    values = [t * 100 + p for t in range(len(times)) for p in range(len(percentiles))]
    return {
        "domain": {
            "axes": {
                "x": {"values": [0.3053]},
                "y": {"values": [51.6214]},
                "t": {"values": times},
                "percentile": {"values": percentiles},
            }
        },
        "parameters": {"airTemperature": {"unit": {"symbol": "K"}}},
        "ranges": {
            "airTemperature": {
                "type": "NdArray",
                "dataType": "float",
                "axisNames": ["t", "percentile"],
                "shape": [len(times), len(percentiles)],
                "values": values,
            }
        },
    }


TIMES = ["2026-08-15T09:00Z", "2026-08-15T10:00Z", "2026-08-15T11:00Z"]
PERCENTILES = [10.0, 50.0, 90.0]


def test_parses_time_axis_with_trailing_z():
    coverage = covjson.parse(build_doc(TIMES, PERCENTILES))
    assert coverage.times[0] == datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
    assert len(coverage.times) == 3


def test_reads_coordinates():
    coverage = covjson.parse(build_doc(TIMES, PERCENTILES))
    assert coverage.axes.latitude == pytest.approx(51.6214)
    assert coverage.axes.longitude == pytest.approx(0.3053)


def test_at_indexes_row_major_by_axis_name():
    coverage = covjson.parse(build_doc(TIMES, PERCENTILES))
    rng = coverage.ranges["airTemperature"]
    # value == t*100 + p, so any transposition shows up immediately.
    assert rng.at(t=0, percentile=0) == 0
    assert rng.at(t=0, percentile=2) == 2
    assert rng.at(t=2, percentile=0) == 200
    assert rng.at(t=1, percentile=1) == 101


def test_series_walks_time_holding_percentile_fixed():
    coverage = covjson.parse(build_doc(TIMES, PERCENTILES))
    assert coverage.series("airTemperature", percentile=1) == [1, 101, 201]
    assert coverage.series("airTemperature", percentile=2) == [2, 102, 202]


def test_series_is_not_a_naive_flat_slice():
    """Guards the specific bug this module exists to prevent."""
    coverage = covjson.parse(build_doc(TIMES, PERCENTILES))
    naive = coverage.ranges["airTemperature"].values[: len(TIMES)]
    assert coverage.series("airTemperature", percentile=0) != naive


def test_transposed_axis_order_is_respected():
    """Same data declared percentile-major must read back identically."""
    doc = build_doc(TIMES, PERCENTILES)
    values = [t * 100 + p for p in range(len(PERCENTILES)) for t in range(len(TIMES))]
    doc["ranges"]["airTemperature"] |= {
        "axisNames": ["percentile", "t"],
        "shape": [len(PERCENTILES), len(TIMES)],
        "values": values,
    }
    coverage = covjson.parse(doc)
    assert coverage.series("airTemperature", percentile=1) == [1, 101, 201]


def test_percentile_axis_discovered_by_elimination():
    coverage = covjson.parse(build_doc(TIMES, PERCENTILES))
    name, values = coverage.percentile_axis()
    assert name == "percentile"
    assert values == [10.0, 50.0, 90.0]


def test_nearest_percentile_index():
    assert covjson.nearest_percentile_index([10.0, 50.0, 90.0], 50) == 1
    assert covjson.nearest_percentile_index([5.0, 45.0, 95.0], 50) == 1
    assert covjson.nearest_percentile_index([10.0, 90.0], 50) in {0, 1}


def test_time_only_range_still_works():
    doc = {
        "domain": {"axes": {"t": {"values": TIMES}}},
        "parameters": {},
        "ranges": {
            "weatherSymbol": {
                "axisNames": ["t"],
                "shape": [3],
                "values": [1, 3, 7],
            }
        },
    }
    coverage = covjson.parse(doc)
    assert coverage.series("weatherSymbol") == [1, 3, 7]


def test_compact_axis_form_is_expanded():
    doc = build_doc(TIMES, PERCENTILES)
    doc["domain"]["axes"]["z"] = {"start": 0.0, "stop": 10.0, "num": 3}
    coverage = covjson.parse(doc)
    assert coverage.axes.others["z"] == [0.0, 5.0, 10.0]


def test_shape_mismatch_is_rejected_loudly():
    doc = build_doc(TIMES, PERCENTILES)
    doc["ranges"]["airTemperature"]["shape"] = [3, 4]
    with pytest.raises(covjson.CovJsonError, match="implies 12 values"):
        covjson.parse(doc)


def test_missing_time_axis_is_rejected():
    doc = build_doc(TIMES, PERCENTILES)
    del doc["domain"]["axes"]["t"]
    with pytest.raises(covjson.CovJsonError, match="no time axis"):
        covjson.parse(doc)


def test_unknown_parameter_returns_none():
    coverage = covjson.parse(build_doc(TIMES, PERCENTILES))
    assert coverage.series("noSuchParameter") is None


# --- Axis order detection -------------------------------------------------------
#
# The live BPF service declares axisNames in row-major order but serialises the
# values column-major. Trusting the declared order yields a 10th percentile above
# the 90th, so the layout is inferred from the ordering invariant instead.


N_PCT, N_TIME = 3, 4


def _truth(p: int, t: int) -> float:
    """Rises with percentile, falls over time.

    The shape must not be square and the time trend must be steeper than the
    percentile spread: otherwise a transposed reading stays monotonic too and the
    two layouts are genuinely indistinguishable.
    """
    return 280.0 + p * 2.0 - t * 3.0


def build_ordered_doc(order: str) -> dict:
    """Percentiles that genuinely increase, laid out in the given memory order."""
    times = [f"2026-08-15T0{3 + i}:00Z" for i in range(N_TIME)]
    pcts = ["10", "50", "90"]

    if order == "C":
        values = [_truth(p, t) for p in range(N_PCT) for t in range(N_TIME)]
    else:
        values = [_truth(p, t) for t in range(N_TIME) for p in range(N_PCT)]

    return {
        "domain": {"axes": {"t": {"values": times}, "percentiles": {"values": pcts}}},
        "parameters": {},
        "ranges": {
            "airTemperature1p5m": {
                "axisNames": ["percentiles", "t"],
                "shape": [N_PCT, N_TIME],
                "values": values,
            }
        },
    }


@pytest.mark.parametrize("order", ["C", "F"])
def test_percentile_order_detected_from_the_data(order):
    coverage = covjson.parse(build_ordered_doc(order))
    assert coverage.ranges["airTemperature1p5m"].order == order


@pytest.mark.parametrize("order", ["C", "F"])
def test_percentiles_read_back_correctly_either_way(order):
    coverage = covjson.parse(build_ordered_doc(order))
    name, values = coverage.percentile_axis()
    p10 = coverage.series("airTemperature1p5m", **{name: 0})
    p50 = coverage.series("airTemperature1p5m", **{name: 1})
    p90 = coverage.series("airTemperature1p5m", **{name: 2})
    assert p10 == [_truth(0, t) for t in range(N_TIME)]
    assert p50 == [_truth(1, t) for t in range(N_TIME)]
    assert p90 == [_truth(2, t) for t in range(N_TIME)]
    # The invariant that drives the detection.
    assert all(a < b < c for a, b, c in zip(p10, p50, p90))


def test_column_major_data_is_not_read_as_declared():
    """Guards the exact bug: the declared order would invert the percentiles."""
    coverage = covjson.parse(build_ordered_doc("F"))
    rng = coverage.ranges["airTemperature1p5m"]
    assert rng.order == "F"
    naive = rng.with_order("C")
    name, _ = coverage.percentile_axis()
    # Under the declared reading the 10th percentile would exceed the 50th.
    assert naive.at(percentiles=0, t=1) > naive.at(percentiles=1, t=1)
    assert rng.at(percentiles=0, t=1) < rng.at(percentiles=1, t=1)


def test_threshold_probabilities_detected_as_decreasing():
    times = ["2026-08-15T03:00Z", "2026-08-15T04:00Z"]
    thresholds = [">0.0", ">2.7777778E-8", ">1.388889E-6"]

    # Probability falls as the threshold rises; laid out column-major.
    def truth(k: int, t: int) -> float:
        return 0.9 - 0.3 * k - 0.05 * t

    doc = {
        "domain": {
            "axes": {
                "t": {"values": times},
                "probabilityOfRainValues": {"values": thresholds},
            }
        },
        "parameters": {},
        "ranges": {
            "probabilityOfRain": {
                "axisNames": ["probabilityOfRainValues", "t"],
                "shape": [3, 2],
                "values": [truth(k, t) for t in range(2) for k in range(3)],
            }
        },
    }
    coverage = covjson.parse(doc)
    assert coverage.ranges["probabilityOfRain"].order == "F"
    name, values = coverage.threshold_axis()
    assert values == pytest.approx([0.0, 2.7777778e-8, 1.388889e-6])
    series = coverage.series("probabilityOfRain", **{name: 0})
    assert series == pytest.approx([0.9, 0.85])


def test_threshold_axis_parses_comparison_prefixes():
    assert covjson._parse_threshold(">2.7777778E-8") == pytest.approx(2.7777778e-8)
    assert covjson._parse_threshold(">=213.15") == pytest.approx(213.15)
    assert covjson._parse_threshold(0.5) == 0.5
    assert covjson._parse_threshold("not a number") is None


def test_nearest_threshold_uses_a_log_scale():
    """Rain-rate thresholds span orders of magnitude; linear matching collapses."""
    thresholds = [0.0, 8.333333e-9, 2.7777778e-8, 6.944445e-8, 1.388889e-6, 0.003]
    target = 0.0001 / 3600  # 0.1 mm/hr
    assert thresholds[covjson.nearest_threshold_index(thresholds, target)] == pytest.approx(
        2.7777778e-8
    )


def test_coverage_collection_parsed():
    doc = {
        "type": "CoverageCollection",
        "coverages": [build_ordered_doc("F"), build_ordered_doc("C")],
    }
    covset = covjson.parse_collection(doc)
    assert "airTemperature1p5m" in covset.parameter_names


def test_single_coverage_wrapped_as_a_collection():
    covset = covjson.parse_collection(build_ordered_doc("C"))
    assert covset.get("airTemperature1p5m") is not None
    assert covset.get("nope") is None


def test_empty_coverage_collection_rejected():
    with pytest.raises(covjson.CovJsonError, match="no readable coverages"):
        covjson.parse_collection({"type": "CoverageCollection", "coverages": []})
