"""Runtime configuration from the environment and an optional .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")

# The Met Office publishes two live versions of this service. The guide describes
# 2.0.0, while the sample client uses 1.0.0. A key may be subscribed to only one,
# so the base URL is configurable and datahub.py can try both.
BASE_URL_V2 = "https://data.hub.api.metoffice.gov.uk/mo-blended-prob-forecast-feature-svc/2.0.0"
BASE_URL_V1 = "https://data.hub.api.metoffice.gov.uk/mo-site-specific-blended-probabilistic-forecast/1.0.0"

# NSWWS is a separate Weather DataHub product. Its Atom feed identifies the current
# warnings snapshot, so callers need only this service root.
NSWWS_BASE_URL = "https://data.hub.api.metoffice.gov.uk/nswws/v1.1"

# These ids were confirmed by scripts/discover.py. They differ from the ids in the
# Met Office sample client, which still uses improver-*-spot-uk.
PERCENTILES_COLLECTION = "uk-spot-percentiles"
PROBABILITIES_COLLECTION = "uk-spot-probabilities"

# Locations and data belong to an instance rather than directly to the collection.
# The current instance is "blended", but it is resolved at runtime.
DEFAULT_INSTANCE = "blended"

# The free UK geocoder, used because BPF locations contain coordinates but no names.
POSTCODES_IO = "https://api.postcodes.io"

UK_TZ = ZoneInfo("Europe/London")

# The default site used when no location is configured. It is the site behind the
# weather.metoffice.gov.uk/forecast/u10jxj0u7 page this project replaces.
DEFAULT_LATITUDE = 51.6214
DEFAULT_LONGITUDE = 0.3053
DEFAULT_SITE_NAME = "Brentwood"


class ConfigError(RuntimeError):
    """Raised when required configuration is not provided."""


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    cache_dir: Path
    cache_ttl: int
    """Fallback cache lifetime, in seconds, for responses not tied to the forecast clock.

    Forecast and instance requests ignore this and expire when the wall-clock hour
    turns instead: the data rolls hourly, its time axis advancing a step, so refetching
    on that boundary keeps the table current at 24 calls a day per site rather than the
    96 a 15-minute TTL would cost. Hours that have already passed are dropped when the
    table is built, in model.py, rather than being left to the cache to expire.
    """
    site_catalogue_ttl: int
    default_site: str | None
    # The warnings subscription is optional. Without it, the forecast still works and
    # the page simply omits warnings.
    nswws_api_key: str = ""
    nswws_base_url: str = NSWWS_BASE_URL
    warnings_cache_ttl: int = 60

    @classmethod
    def from_env(cls) -> Config:
        api_key = os.environ.get("METOFFICE_API_KEY", "").strip()
        if not api_key:
            raise ConfigError(
                "METOFFICE_API_KEY is not set. Copy .env.example to .env and add your "
                "Met Office DataHub API key."
            )
        return cls(
            api_key=api_key,
            base_url=os.environ.get("WBL_BASE_URL", BASE_URL_V2).rstrip("/"),
            cache_dir=Path(os.environ.get("WBL_CACHE_DIR", PROJECT_ROOT / ".cache")),
            cache_ttl=int(os.environ.get("WBL_CACHE_TTL", "900")),
            site_catalogue_ttl=int(os.environ.get("WBL_SITE_TTL", str(7 * 24 * 3600))),
            default_site=os.environ.get("WBL_DEFAULT_SITE") or None,
            nswws_api_key=os.environ.get("METOFFICE_NSWWS_API_KEY", "").strip(),
            nswws_base_url=os.environ.get("WBL_NSWWS_BASE_URL", NSWWS_BASE_URL).rstrip("/"),
            warnings_cache_ttl=int(os.environ.get("WBL_WARNINGS_TTL", "60")),
        )
