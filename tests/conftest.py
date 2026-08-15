"""Shared pytest fixtures. Synthetic API documents live in bpf_fixtures.py."""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest

from weather_bureau_light.config import UK_TZ
from weather_bureau_light.datahub import Fetched

from bpf_fixtures import (
    COLLECTIONS,
    INSTANCES,
    LOCATIONS,
    PLACES_RESPONSE,
    POSTCODE_RESPONSE,
    REVERSE_RESPONSE,
    build_percentile_doc,
    build_probability_doc,
)


class FakeClient:
    """Stands in for DataHubClient, recording calls so caching can be asserted."""

    base_url = "https://example.invalid/fake/2.0.0"

    def __init__(self) -> None:
        self.calls: list[str] = []
        # Set by tests that need the app to behave as though the API were down.
        self.stale = False
        self.retrieved_at: datetime | None = None
        self.last_success_at: datetime | None = datetime.now(UK_TZ)
        self.last_failure_at: datetime | None = None
        self.last_failure: str | None = None

    def serving_stale(self, age_hours: float, message: str = "HTTP 403") -> None:
        """Behave as the real client does when a fetch fails but a cache exists."""
        now = datetime.now(UK_TZ)
        self.stale = True
        self.retrieved_at = now - timedelta(hours=age_hours)
        self.last_failure_at = now
        self.last_failure = message
        self.last_success_at = self.retrieved_at

    def ensure_base_url(self) -> str:
        return self.base_url

    def collections(self) -> dict:
        self.calls.append("collections")
        return COLLECTIONS

    def collection(self, collection_id: str) -> dict | None:
        return next((c for c in COLLECTIONS["collections"] if c["id"] == collection_id), None)

    def instance(self, collection_id: str) -> str:
        return INSTANCES["instances"][0]["id"]

    def locations(self, collection_id: str) -> dict:
        self.calls.append(f"locations:{collection_id}")
        return LOCATIONS

    def forecast(self, collection_id: str, location_id: str, parameters=None) -> Fetched:
        self.calls.append(f"forecast:{collection_id}:{location_id}")
        if "probabilities" in collection_id:
            payload = build_probability_doc(parameters)
        else:
            payload = build_percentile_doc(parameters)
        return Fetched(
            payload,
            retrieved_at=self.retrieved_at or datetime.now(UK_TZ),
            stale=self.stale,
        )

    def close(self) -> None:
        pass


def geocoder_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.startswith("/postcodes/"):
        wanted = POSTCODE_RESPONSE["result"]["postcode"].replace(" ", "").upper()
        if path.rsplit("/", 1)[-1].upper() == wanted:
            return httpx.Response(200, json=POSTCODE_RESPONSE)
        return httpx.Response(404, json={"status": 404, "result": None})
    if path == "/postcodes":  # reverse geocoding
        return httpx.Response(200, json=REVERSE_RESPONSE)
    if path == "/places":
        query = (request.url.params.get("q") or "").lower()
        matches = [
            r for r in PLACES_RESPONSE["result"] if query in r["name_1"].lower()
        ]
        return httpx.Response(200, json={"status": 200, "result": matches})
    return httpx.Response(404, json={"status": 404, "result": None})


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def config(tmp_path):
    from weather_bureau_light.config import Config

    return Config(
        api_key="test-key",
        base_url="https://example.invalid/fake/2.0.0",
        cache_dir=tmp_path / "cache",
        cache_ttl=900,
        site_catalogue_ttl=604800,
        default_site=None,
    )


@pytest.fixture
def geocoder(config):
    from weather_bureau_light.datahub import DiskCache
    from weather_bureau_light.geocode import Geocoder

    return Geocoder(
        DiskCache(config.cache_dir),
        client=httpx.Client(transport=httpx.MockTransport(geocoder_handler)),
        base_url="https://api.postcodes.io",
    )


@pytest.fixture
def service(config, fake_client, geocoder):
    from weather_bureau_light.service import ForecastService

    return ForecastService(config, client=fake_client, geocoder=geocoder)


@pytest.fixture
def client(config, service):
    from weather_bureau_light.app import create_app

    app = create_app(config=config, service=service)
    app.config["TESTING"] = True
    return app.test_client()
