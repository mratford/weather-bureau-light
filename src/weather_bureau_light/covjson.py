"""Parse CoverageJSON into values indexed by time and percentile.

Each range contains a flattened N-dimensional array described by `axisNames` and
`shape`. Percentile data has both a percentile axis and a time axis, so values must
be indexed using the declared dimensions rather than paired directly with time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Iterator, Sequence

log = logging.getLogger(__name__)


class CovJsonError(ValueError):
    """Raised when a document does not have the expected CoverageJSON structure."""


_COORDINATE_AXES = {"x", "y", "z", "t"}


def _parse_threshold(value: Any) -> float | None:
    """Convert a threshold axis label such as '>2.7777778E-8' or '>=213.15'."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        return float(value.lstrip("><= "))
    except ValueError:
        return None


def nearest_threshold_index(thresholds: Sequence[float], wanted: float) -> int:
    """Return the closest threshold index, comparing values on a log scale.

    Rain-rate thresholds span several orders of magnitude, so a linear comparison
    would favour the largest values.
    """
    import math

    def distance(value: float) -> float:
        if value <= 0 or wanted <= 0:
            return abs(value - wanted)
        return abs(math.log10(value) - math.log10(wanted))

    return min(range(len(thresholds)), key=lambda i: distance(thresholds[i]))


def _axis_values(axis: dict[str, Any]) -> list[Any]:
    """Return axis values, expanding the compact start/stop/num form when needed."""
    if "values" in axis:
        return list(axis["values"])
    if {"start", "stop", "num"} <= axis.keys():
        start, stop, num = axis["start"], axis["stop"], axis["num"]
        if num == 1:
            return [start]
        step = (stop - start) / (num - 1)
        return [start + i * step for i in range(num)]
    raise CovJsonError(f"axis has neither values nor start/stop/num: {sorted(axis)}")


def parse_time(value: str) -> datetime:
    """Parse an ISO 8601 instant returned by the API."""
    text = value.replace("Z", "+00:00")
    return datetime.fromisoformat(text)


@dataclass(frozen=True)
class Axes:
    """The domain axes of a coverage, ready for indexing ranges."""

    times: list[datetime]
    others: dict[str, list[Any]] = field(default_factory=dict)

    @property
    def latitude(self) -> float | None:
        values = self.others.get("y")
        return values[0] if values else None

    @property
    def longitude(self) -> float | None:
        values = self.others.get("x")
        return values[0] if values else None


def parse_axes(doc: dict[str, Any]) -> Axes:
    domain = doc.get("domain")
    if not isinstance(domain, dict):
        raise CovJsonError("document has no domain")
    raw_axes = domain.get("axes")
    if not isinstance(raw_axes, dict):
        raise CovJsonError("domain has no axes")

    times: list[datetime] = []
    others: dict[str, list[Any]] = {}
    for name, axis in raw_axes.items():
        if not isinstance(axis, dict):
            continue
        values = _axis_values(axis)
        if name == "t":
            times = [parse_time(v) if isinstance(v, str) else v for v in values]
        else:
            others[name] = values
    if not times:
        raise CovJsonError("domain has no time axis")
    return Axes(times=times, others=others)


def _strides(shape: Sequence[int], order: str = "C") -> list[int]:
    """Return strides for a flattened array.

    CoverageJSON specifies row-major ("C"), but the BPF service uses column-major
    ("F") while declaring row-major axis names. Both layouts are supported.
    """
    strides = [1] * len(shape)
    if order == "C":
        for i in range(len(shape) - 2, -1, -1):
            strides[i] = strides[i + 1] * shape[i + 1]
    else:
        for i in range(1, len(shape)):
            strides[i] = strides[i - 1] * shape[i - 1]
    return strides


@dataclass(frozen=True)
class Range:
    """Values for one parameter, addressable by named axis indices."""

    parameter: str
    axis_names: list[str]
    shape: list[int]
    values: list[Any]
    order: str = "C"

    def __post_init__(self) -> None:
        if len(self.axis_names) != len(self.shape):
            raise CovJsonError(
                f"{self.parameter}: axisNames {self.axis_names} does not match shape {self.shape}"
            )
        expected = 1
        for dim in self.shape:
            expected *= dim
        if len(self.values) != expected:
            raise CovJsonError(
                f"{self.parameter}: shape {self.shape} implies {expected} values, "
                f"got {len(self.values)}"
            )

    def with_order(self, order: str) -> Range:
        return self if order == self.order else replace(self, order=order)

    def at(self, **indices: int) -> Any:
        """Look up one value by axis name; omitted axes must have length one."""
        flat = 0
        for axis, stride, size in zip(
            self.axis_names, _strides(self.shape, self.order), self.shape
        ):
            index = indices.get(axis, 0)
            if index < 0:
                index += size
            if not 0 <= index < size:
                raise IndexError(f"{self.parameter}: {axis}={index} out of range 0..{size - 1}")
            flat += index * stride
        return self.values[flat]

    def series(self, **fixed: int) -> list[Any]:
        """Return all time-axis values with the other axes fixed."""
        if "t" not in self.axis_names:
            # Parameters without a time dimension provide one value to repeat.
            return [self.at(**fixed)]
        length = self.shape[self.axis_names.index("t")]
        return [self.at(t=i, **fixed) for i in range(length)]


def _violations(rng: Range, axis: str, increasing: bool) -> int:
    """Count timesteps where values along `axis` violate the expected order."""
    if axis not in rng.axis_names or "t" not in rng.axis_names:
        return 0
    n_axis = rng.shape[rng.axis_names.index(axis)]
    n_time = rng.shape[rng.axis_names.index("t")]
    bad = 0
    for t in range(n_time):
        series = []
        for i in range(n_axis):
            value = rng.at(**{axis: i, "t": t})
            if isinstance(value, (int, float)):
                series.append(value)
        for a, b in zip(series, series[1:]):
            if (b < a - 1e-9) if increasing else (b > a + 1e-9):
                bad += 1
                break
    return bad


def choose_order(rng: Range, axis: str, increasing: bool = True) -> Range:
    """Choose the memory layout that best matches the data's ordering.

    The BPF service declares `axisNames` in row-major order but serialises the values
    column-major. The correct layout has increasing percentiles or decreasing
    probabilities, as appropriate.
    """
    candidates = [rng.with_order(order) for order in ("C", "F")]
    scored = [(_violations(c, axis, increasing), c) for c in candidates]
    best_score = min(score for score, _ in scored)
    # Prefer the order declared by the specification when scores tie.
    for score, candidate in scored:
        if score == best_score:
            if candidate.order != rng.order:
                log.debug(
                    "%s: reading %s-order (%d violations vs %d)",
                    rng.parameter,
                    candidate.order,
                    best_score,
                    max(s for s, _ in scored),
                )
            return candidate
    return rng


def parse_ranges(doc: dict[str, Any]) -> dict[str, Range]:
    ranges = doc.get("ranges")
    if not isinstance(ranges, dict):
        raise CovJsonError("document has no ranges")

    parsed: dict[str, Range] = {}
    for name, raw in ranges.items():
        if not isinstance(raw, dict):
            continue
        values = raw.get("values")
        if values is None:
            continue
        axis_names = list(raw.get("axisNames") or ["t"])
        shape = list(raw.get("shape") or [len(values)])
        parsed[name] = Range(parameter=name, axis_names=axis_names, shape=shape, values=values)
    return parsed


@dataclass(frozen=True)
class Coverage:
    """A parsed CoverageJSON document with its axes and ranges."""

    axes: Axes
    ranges: dict[str, Range]
    parameters: dict[str, Any]

    @property
    def times(self) -> list[datetime]:
        return self.axes.times

    def percentile_axis(self) -> tuple[str, list[float]] | None:
        """Find the percentile axis, including alternate service names.

        The UK collection calls it `percentiles` and stores its values as strings.
        If that name is absent, use the first non-coordinate axis with numeric values.
        """
        for name, values in self.axes.others.items():
            if "percentile" in name.lower():
                return name, [float(v) for v in values]
        for name, values in self.axes.others.items():
            if name in _COORDINATE_AXES or name == "locationId":
                continue
            try:
                return name, [float(v) for v in values]
            except (TypeError, ValueError):
                continue
        return None

    def threshold_axis(self) -> tuple[str, list[float]] | None:
        """Find the axis containing probability thresholds.

        Probability parameters use string values such as ">2.7777778E-8", in m/s.
        """
        for name, values in self.axes.others.items():
            if "threshold" not in name.lower() and not name.lower().endswith("values"):
                continue
            parsed = [_parse_threshold(v) for v in values]
            if all(v is not None for v in parsed):
                return name, [v for v in parsed if v is not None]
        return None

    def series(self, parameter: str, **fixed: int) -> list[Any] | None:
        rng = self.ranges.get(parameter)
        return None if rng is None else rng.series(**fixed)

    def iter_parameters(self) -> Iterator[tuple[str, dict[str, Any]]]:
        for name, meta in self.parameters.items():
            if isinstance(meta, dict):
                yield name, meta


def parse(doc: dict[str, Any]) -> Coverage:
    coverage = Coverage(
        axes=parse_axes(doc),
        ranges=parse_ranges(doc),
        parameters=doc.get("parameters") or {},
    )

    # Check the declared axis order against the expected value ordering.
    percentile_axis = coverage.percentile_axis()
    threshold_axis = coverage.threshold_axis()
    for name, rng in list(coverage.ranges.items()):
        if percentile_axis and percentile_axis[0] in rng.axis_names:
            coverage.ranges[name] = choose_order(rng, percentile_axis[0], increasing=True)
        elif threshold_axis and threshold_axis[0] in rng.axis_names:
            # The probability of exceeding a threshold decreases as the threshold rises.
            coverage.ranges[name] = choose_order(rng, threshold_axis[0], increasing=False)
    return coverage


@dataclass(frozen=True)
class CoverageSet:
    """A CoverageCollection with one coverage per parameter and its own domain.

    The live API returns different time axes for hourly and three-hourly parameters,
    so each parameter keeps its own axis and callers merge them by timestamp.
    """

    coverages: dict[str, Coverage]

    def get(self, parameter: str) -> Coverage | None:
        return self.coverages.get(parameter)

    @property
    def parameter_names(self) -> list[str]:
        return list(self.coverages)

    def __len__(self) -> int:
        return len(self.coverages)


def parse_collection(doc: dict[str, Any]) -> CoverageSet:
    """Parse a CoverageCollection or a single Coverage into a CoverageSet."""
    if doc.get("type") != "CoverageCollection" and "coverages" not in doc:
        coverage = parse(doc)
        return CoverageSet({name: coverage for name in coverage.ranges})

    coverages: dict[str, Coverage] = {}
    for entry in doc.get("coverages") or []:
        if not isinstance(entry, dict):
            continue
        # Shared definitions may be stored on the collection.
        merged = dict(entry)
        if "parameters" not in merged and "parameters" in doc:
            merged["parameters"] = doc["parameters"]
        try:
            coverage = parse(merged)
        except CovJsonError:
            continue
        for name in coverage.ranges:
            coverages[name] = coverage

    if not coverages:
        raise CovJsonError("CoverageCollection contained no readable coverages")
    return CoverageSet(coverages)


def nearest_percentile_index(percentiles: Sequence[float], wanted: float) -> int:
    """Return the index of the closest available percentile.

    Collections do not all publish the same percentile set, so the nearest available
    value is used instead of assuming a fixed list.
    """
    if not percentiles:
        raise CovJsonError("no percentiles available")
    return min(range(len(percentiles)), key=lambda i: abs(percentiles[i] - wanted))
