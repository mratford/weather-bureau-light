"""Shared pytest fixtures. Synthetic API documents live in bpf_fixtures.py."""

from __future__ import annotations

import httpx
import pytest

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

    def __init__(self) -> None:
        self.calls: list[str] = []

    def ensure_base_url(self) -> str:
        return "https://example.invalid/fake/2.0.0"

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

    def forecast(self, collection_id: str, location_id: str, parameters=None) -> dict:
        self.calls.append(f"forecast:{collection_id}:{location_id}")
        if "probabilities" in collection_id:
            return build_probability_doc(parameters)
        return build_percentile_doc(parameters)

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
