"""Assemble parsed coverages into the shape the template renders.

Two collections feed one table: percentiles carry temperature, wind, humidity,
visibility, UV, pressure and the weather symbol; the chance-of-precipitation row is a
probability and comes from the probabilities collection. They are merged on timestamp,
because the two collections do not necessarily publish identical time axes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import astro, covjson, symbols, units
from .covjson import Coverage, CoverageSet
from .parameters import PERCENTILE_FIELDS, PROBABILITY_FIELDS, Field, Resolution
from .sites import Site

MEDIAN = 50.0
LOWER = 10.0
UPPER = 90.0

#: How many days the day strip shows. The API returns about fourteen, but the later ones
#: carry no weather symbol and a spread wide enough to be worth little.
MAX_DAYS = 7

#: 0.1 mm/hr expressed in m/s, the Met Office's threshold for "any precipitation".
PRECIP_THRESHOLD_MS = 0.0001 / 3600


@dataclass
class Value:
    """One cell: a median, plus the 10th-90th spread where the data is probabilistic."""

    median: float | None = None
    lower: float | None = None
    upper: float | None = None

    @property
    def has_range(self) -> bool:
        return (
            self.lower is not None
            and self.upper is not None
            and round(self.lower) != round(self.upper)
        )

    def rounded(self) -> int | None:
        return None if self.median is None else int(round(self.median))

    def range_text(self, unit: str = "") -> str:
        if not self.has_range:
            return "—"
        return f"{int(round(self.lower))} to {int(round(self.upper))}{unit}"


@dataclass
class Timestep:
    """One column of the forecast table."""

    time: datetime
    values: dict[str, Value] = field(default_factory=dict)
    is_daylight: bool = True

    def value(self, key: str) -> Value:
        return self.values.get(key, Value())

    def median(self, key: str) -> float | None:
        return self.value(key).median

    def rounded(self, key: str) -> int | None:
        return self.value(key).rounded()

    @property
    def label(self) -> str:
        return self.time.strftime("%H:%M")

    @property
    def symbol(self) -> symbols.Symbol:
        code = self.median("weather_code")
        resolved = symbols.lookup(code)
        if resolved is symbols.UNKNOWN:
            return resolved
        # Fall back to our own daylight calculation when the code is a day/night pair
        # but the reported variant disagrees with the actual local time.
        if resolved.night and self.is_daylight:
            for candidate in symbols._SYMBOLS.values():
                if candidate.sprite == resolved.sprite and not candidate.night:
                    return candidate
        return resolved

    @property
    def wind_direction_text(self) -> str | None:
        return units.compass_point(self.median("wind_direction"))

    @property
    def visibility_band(self) -> units.Band | None:
        return units.visibility_band(self.median("visibility"))

    @property
    def uv_band(self) -> str | None:
        return units.uv_band(self.median("uv"))

    @property
    def gust_is_strong(self) -> bool:
        gust = self.median("wind_gust")
        return gust is not None and gust >= units.STRONG_GUST_MPH


@dataclass
class Day:
    """One tab in the day strip."""

    date: date
    timesteps: list[Timestep] = field(default_factory=list)
    sunrise: datetime | None = None
    sunset: datetime | None = None

    @property
    def max_temp(self) -> int | None:
        values = [t.median("temperature") for t in self.timesteps]
        values = [v for v in values if v is not None]
        return int(round(max(values))) if values else None

    @property
    def min_temp(self) -> int | None:
        values = [t.median("temperature") for t in self.timesteps]
        values = [v for v in values if v is not None]
        return int(round(min(values))) if values else None

    @property
    def symbol(self) -> symbols.Symbol:
        """Representative symbol: whatever is forecast around the middle of the day."""
        daytime = [t for t in self.timesteps if t.is_daylight] or self.timesteps
        if not daytime:
            return symbols.UNKNOWN
        midday = min(daytime, key=lambda t: abs(t.time.hour - 13))
        return midday.symbol

    @property
    def iso(self) -> str:
        return self.date.isoformat()

    @property
    def tab_label(self) -> str:
        return self.date.strftime("%a %-d %b")


@dataclass
class Forecast:
    site: Site
    days: list[Day]
    issued: datetime | None = None
    missing_fields: list[str] = field(default_factory=list)

    def day(self, iso: str | None) -> Day | None:
        if not self.days:
            return None
        if iso is None:
            return self.days[0]
        return next((d for d in self.days if d.iso == iso), self.days[0])


def _extract(
    coverages: CoverageSet, resolution: Resolution, specs, into: dict[str, dict[datetime, Value]]
) -> None:
    """Pull each resolved field off its own coverage and index it by timestamp.

    Each parameter has its own time axis - and in the three-hourly part of the
    forecast those axes are offset from one another (one parameter on 01:00/04:00,
    another on 02:00/05:00) - so each field keeps its own timestamp map and the
    columns are reconciled later in `build`.
    """
    for spec in specs:
        # Finest resolution first; coarser sources only fill timesteps it does not cover.
        for name in resolution.names_for(spec.key):
            coverage = coverages.get(name)
            if coverage is not None:
                _extract_one(coverage, name, spec, into)


def _extract_one(
    coverage: Coverage, name: str, spec: Field, into: dict[str, dict[datetime, Value]]
) -> None:
    """Read one parameter into the field's timestamp map."""
    percentile_axis = coverage.percentile_axis() if spec.probabilistic else None
    fixed: dict[str, int] = {}

    # Probability parameters are published per threshold. The Met Office's chance of
    # precipitation means "any precipitation", which is the 0.1 mm/hr threshold,
    # expressed here in m/s.
    threshold_axis = coverage.threshold_axis()
    if threshold_axis is not None:
        axis_name, thresholds = threshold_axis
        fixed[axis_name] = covjson.nearest_threshold_index(thresholds, PRECIP_THRESHOLD_MS)

    def read(target: float) -> list[float | None] | None:
        if percentile_axis is None:
            return coverage.series(name, **fixed)
        axis_name, values = percentile_axis
        index = covjson.nearest_percentile_index(values, target)
        return coverage.series(name, **{axis_name: index}, **fixed)

    median_series = read(MEDIAN)
    if median_series is None:
        return
    lower_series = read(LOWER) if percentile_axis else None
    upper_series = read(UPPER) if percentile_axis else None

    convert = spec.convert or (lambda v: v)
    series = into.setdefault(spec.key, {})

    for i, moment in enumerate(coverage.times):
        if i >= len(median_series):
            break
        # A timestep already filled by a finer-resolution source wins.
        existing = series.get(moment)
        if existing is not None and existing.median is not None:
            continue
        series[moment] = Value(
            median=convert(median_series[i]),
            lower=convert(lower_series[i]) if lower_series and i < len(lower_series) else None,
            upper=convert(upper_series[i]) if upper_series and i < len(upper_series) else None,
        )


#: Fields ranked by how well they define the table's time grid. Temperature is the
#: backbone of the forecast table, so its timesteps become the columns.
_ANCHOR_PREFERENCE = ("temperature", "feels_like", "wind_speed", "humidity", "pressure")


def _anchor_grid(by_field: dict[str, dict[datetime, Value]]) -> list[datetime]:
    """Choose the timestamps that become table columns.

    Parameters do not share one time axis: in the three-hourly part of the forecast
    some sit on 01:00/04:00 and others on 02:00/05:00. Taking the union would produce
    a column per grid, half of them nearly empty, so one field's axis is adopted as
    the grid and the rest are matched onto it.
    """
    for key in _ANCHOR_PREFERENCE:
        stamps = [m for m, v in by_field.get(key, {}).items() if v.median is not None]
        if stamps:
            return sorted(stamps)
    # No preferred field resolved; fall back to whichever has the most timesteps.
    richest = max(by_field.values(), key=len, default={})
    return sorted(m for m, v in richest.items() if v.median is not None)


def _column(
    by_field: dict[str, dict[datetime, Value]], moment: datetime, grid: list[datetime]
) -> dict[str, Value]:
    """Assemble one table column, matching off-grid fields to the nearest timestamp.

    A field on an offset grid is still worth showing, so it is allowed to contribute
    a value up to half a column-width away rather than being dropped.
    """
    tolerance = _half_spacing(grid, moment)
    column: dict[str, Value] = {}
    for key, series in by_field.items():
        value = series.get(moment)
        if value is None or value.median is None:
            candidates = [
                (abs((stamp - moment).total_seconds()), stamp)
                for stamp, v in series.items()
                if v.median is not None and abs((stamp - moment).total_seconds()) <= tolerance
            ]
            if not candidates:
                continue
            value = series[min(candidates)[1]]
        column[key] = value
    return column


def _half_spacing(grid: list[datetime], moment: datetime) -> float:
    """Half the gap to the neighbouring column, in seconds."""
    if len(grid) < 2:
        return 3600.0
    index = grid.index(moment)
    gaps = []
    if index > 0:
        gaps.append((moment - grid[index - 1]).total_seconds())
    if index + 1 < len(grid):
        gaps.append((grid[index + 1] - moment).total_seconds())
    return min(gaps) / 2 if gaps else 3600.0


def build(
    site: Site,
    percentiles: CoverageSet,
    percentile_resolution: Resolution,
    probabilities: CoverageSet | None = None,
    probability_resolution: Resolution | None = None,
    tz: ZoneInfo | None = None,
    issued: datetime | None = None,
    max_days: int = MAX_DAYS,
) -> Forecast:
    """Merge the coverages into days of timesteps, in local time."""
    tz = tz or ZoneInfo("Europe/London")

    by_field: dict[str, dict[datetime, Value]] = {}
    _extract(percentiles, percentile_resolution, PERCENTILE_FIELDS, by_field)
    if probabilities is not None and probability_resolution is not None:
        _extract(probabilities, probability_resolution, PROBABILITY_FIELDS, by_field)

    missing = list(percentile_resolution.missing)
    if probability_resolution is not None:
        missing += probability_resolution.missing

    grid = _anchor_grid(by_field)
    by_time = {moment: _column(by_field, moment, grid) for moment in grid}

    # Group into local days. Grouping after conversion to local time is what makes
    # this correct across a DST boundary, where a day is 23 or 25 hours long.
    days: dict[date, Day] = {}
    for moment in sorted(by_time):
        local = moment.astimezone(tz)
        day = days.get(local.date())
        if day is None:
            sunrise, sunset = astro.sun_times(local.date(), site.latitude, site.longitude, tz)
            day = days[local.date()] = Day(date=local.date(), sunrise=sunrise, sunset=sunset)
        daylight = True
        if day.sunrise and day.sunset:
            daylight = day.sunrise <= local <= day.sunset
        day.timesteps.append(
            Timestep(time=local, values=by_time[moment], is_daylight=daylight)
        )

    # Truncated from the far end, so the strip always starts at today.
    ordered = [days[key] for key in sorted(days)][:max_days]

    return Forecast(
        site=site,
        days=ordered,
        issued=issued,
        missing_fields=missing,
    )
