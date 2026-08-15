"""Runtime configuration, read from the environment and an optional .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")

# The Met Office publishes two live versions of this service. The 2.0.0 service is
# the one the linked user guide documents; the 1.0.0 service is what their own
# sample client still targets. Keys are not always subscribed to both, so the base
# URL stays overridable and datahub.py can fall back.
BASE_URL_V2 = "https://data.hub.api.metoffice.gov.uk/mo-blended-prob-forecast-feature-svc/2.0.0"
BASE_URL_V1 = "https://data.hub.api.metoffice.gov.uk/mo-site-specific-blended-probabilistic-forecast/1.0.0"

# Confirmed live via scripts/discover.py. Note these differ from the ids in the Met
# Office's own published sample client, which still names them improver-*-spot-uk.
PERCENTILES_COLLECTION = "uk-spot-percentiles"
PROBABILITIES_COLLECTION = "uk-spot-probabilities"

# Locations and data hang off an instance rather than the collection directly. Only one
# instance exists ("blended"), but it is resolved at runtime rather than hardcoded.
DEFAULT_INSTANCE = "blended"

# The UK's free geocoder, used because the BPF location list carries coordinates only.
POSTCODES_IO = "https://api.postcodes.io"

UK_TZ = ZoneInfo("Europe/London")

# Brentwood (Essex) - the site behind the weather.metoffice.gov.uk/forecast/u10jxj0u7
# link this project was built to replace. Used to pick a default site when none is set.
DEFAULT_LATITUDE = 51.6214
DEFAULT_LONGITUDE = 0.3053
DEFAULT_SITE_NAME = "Brentwood"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    cache_dir: Path
    cache_ttl: int
    """Fallback TTL, in seconds, for responses that are not tied to the forecast clock.

    Forecast and instance requests ignore this and expire when the wall-clock hour
    turns instead: the data rolls hourly, its time axis advancing a step, so refetching
    on that boundary keeps the table current at 24 calls a day per site rather than the
    96 a 15-minute TTL would cost. Hours that have already passed are dropped when the
    table is built, in model.py, rather than being left to the cache to expire.
    """
    site_catalogue_ttl: int
    default_site: str | None

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
        )
