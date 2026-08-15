"""Tests for the HTTP client: auth header, caching, retries and stale fallback."""

from __future__ import annotations

import dataclasses
import time

import httpx
import pytest

from weather_bureau_light.config import BASE_URL_V1, BASE_URL_V2
from weather_bureau_light.datahub import (
    AuthError,
    DataHubClient,
    DataHubError,
    DiskCache,
    _local,
)


def make_client(config, handler) -> DataHubClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(
        transport=transport,
        headers={"apikey": config.api_key, "accept": "application/json"},
    )
    return DataHubClient(config, client=http)


def test_sends_apikey_header(config):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["apikey"] = request.headers.get("apikey")
        return httpx.Response(200, json={"ok": True})

    make_client(config, handler).get("/collections")
    assert seen["apikey"] == "test-key"


def test_response_is_cached_so_quota_is_not_burned(config):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"n": len(calls)})

    client = make_client(config, handler)
    first = client.get("/collections")
    second = client.get("/collections")
    assert first == second
    assert len(calls) == 1, "second request should have been served from cache"


def test_different_params_cache_separately(config):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"n": len(calls)})

    client = make_client(config, handler)
    client.get("/x", {"parameter-name": "a"})
    client.get("/x", {"parameter-name": "b"})
    assert len(calls) == 2


def test_expired_cache_refetches(config):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"n": len(calls)})

    client = make_client(config, handler)
    client.get("/x")
    client.get("/x", ttl=0)  # immediately stale
    assert len(calls) == 2


def test_401_raises_auth_error_with_guidance(config):
    client = make_client(config, lambda r: httpx.Response(401, text="denied"))
    with pytest.raises(AuthError, match="METOFFICE_API_KEY"):
        client.get("/collections")


def test_403_raises_auth_error(config):
    client = make_client(config, lambda r: httpx.Response(403))
    with pytest.raises(AuthError):
        client.get("/collections")


def test_429_mentions_the_daily_cap(config):
    client = make_client(config, lambda r: httpx.Response(429))
    with pytest.raises(DataHubError, match="Rate limit"):
        client.get("/collections")


def test_404_raises(config):
    client = make_client(config, lambda r: httpx.Response(404))
    with pytest.raises(DataHubError, match="Not found"):
        client.get("/collections")


def test_server_error_is_retried(config, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"ok": True}) if len(calls) >= 3 else httpx.Response(503)

    assert make_client(config, handler).get("/collections") == {"ok": True}
    assert len(calls) == 3


def test_stale_cache_is_served_when_the_api_fails(config, monkeypatch):
    """A forecast a few hours old beats an error page."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    state = {"fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            return httpx.Response(500)
        return httpx.Response(200, json={"good": True})

    client = make_client(config, handler)
    client.get("/x")
    state["fail"] = True
    assert client.get("/x", ttl=0) == {"good": True}


def test_error_raised_when_nothing_cached(config, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    client = make_client(config, lambda r: httpx.Response(500))
    with pytest.raises(DataHubError):
        client.get("/never-fetched")


def test_ensure_base_url_falls_back_to_a_version_the_key_accepts(config):
    """Keys are not always subscribed to both live service versions."""
    config = dataclasses.replace(config, base_url=BASE_URL_V2)

    def handler(request: httpx.Request) -> httpx.Response:
        if "mo-blended-prob-forecast-feature-svc" in str(request.url):
            return httpx.Response(403)
        return httpx.Response(200, json={"collections": []})

    client = make_client(config, handler)
    assert client.ensure_base_url() == BASE_URL_V1


def test_ensure_base_url_raises_when_no_version_works(config):
    client = make_client(config, lambda r: httpx.Response(401))
    with pytest.raises(AuthError, match="No API version"):
        client.ensure_base_url()


def test_disk_cache_survives_corrupt_file(tmp_path):
    cache = DiskCache(tmp_path)
    cache.set("k", {"a": 1})
    corrupt = next(tmp_path.glob("*.json"))
    corrupt.write_text("{not json")
    assert cache.get("k", ttl=999) is None


def test_disk_cache_miss_returns_none(tmp_path):
    assert DiskCache(tmp_path).get("absent", ttl=999) is None


def test_forecast_passes_parameter_name_filter(config):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = str(request.url.query)
        return httpx.Response(200, json={})

    make_client(config, handler).forecast("coll", "0000046", ["airTemperature", "uvIndex"])
    assert "parameter-name" in seen["query"]
    assert "airTemperature" in seen["query"]


# --- Hour-aligned expiry --------------------------------------------------------
#
# The forecast rolls on the hour: its time axis advances a step and the leading
# columns drop off. Forecast and instance responses therefore expire on that
# boundary rather than after a fixed interval.


def at(hour: float) -> float:
    """An epoch timestamp at a given hour offset from a clean hour boundary."""
    base = 1_786_752_000.0  # an exact hour boundary
    return base + hour * 3600


def write_at(cache: DiskCache, key: str, value, when: float) -> None:
    """Seed the cache as though it had been written at a given time."""
    import weather_bureau_light.datahub as dh

    real = dh.time.time
    dh.time.time = lambda: when
    try:
        cache.set(key, value)
    finally:
        dh.time.time = real


def test_cache_is_fresh_within_the_same_hour(tmp_path, monkeypatch):
    cache = DiskCache(tmp_path)
    # Written at 09:06, read at 09:50: same hour, still fresh.
    write_at(cache, "k", {"v": 1}, at(9.10))
    monkeypatch.setattr(time, "time", lambda: at(9.83))
    assert cache.get("k", ttl=0, hour_aligned=True) == {"v": 1}


def test_cache_expires_once_the_hour_turns(tmp_path, monkeypatch):
    cache = DiskCache(tmp_path)
    # 09:06 -> 10:01 crosses the boundary.
    write_at(cache, "k", {"v": 1}, at(9.10))
    monkeypatch.setattr(time, "time", lambda: at(10.02))
    assert cache.get("k", ttl=10**9, hour_aligned=True) is None


def test_floor_prevents_thrash_at_the_boundary(tmp_path, monkeypatch):
    """A fetch at 09:59 must not expire sixty seconds later."""
    cache = DiskCache(tmp_path)
    write_at(cache, "k", {"v": 1}, at(9.99))
    monkeypatch.setattr(time, "time", lambda: at(10.01))
    assert cache.get("k", ttl=0, hour_aligned=True) == {"v": 1}


def test_floor_does_not_hold_data_past_its_hour(tmp_path, monkeypatch):
    cache = DiskCache(tmp_path)
    write_at(cache, "k", {"v": 1}, at(9.99))
    # Beyond the five-minute floor, the boundary wins.
    monkeypatch.setattr(time, "time", lambda: at(10.20))
    assert cache.get("k", ttl=10**9, hour_aligned=True) is None


def test_fixed_ttl_still_applies_when_not_hour_aligned(tmp_path, monkeypatch):
    cache = DiskCache(tmp_path)
    write_at(cache, "k", {"v": 1}, at(9.0))
    monkeypatch.setattr(time, "time", lambda: at(9.5))
    assert cache.get("k", ttl=900) is None
    assert cache.get("k", ttl=10**9) == {"v": 1}


def test_forecast_requests_are_hour_aligned(config, monkeypatch):
    """A forecast fetched twice in the same hour must hit the API once."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "/instances" in str(request.url) and "/locations" not in str(request.url):
            return httpx.Response(200, json={"instances": [{"id": "blended"}]})
        return httpx.Response(200, json={"type": "CoverageCollection", "coverages": []})

    client = make_client(config, handler)
    monkeypatch.setattr(time, "time", lambda: at(9.10))
    client.forecast("uk-spot-percentiles", "00350584")
    monkeypatch.setattr(time, "time", lambda: at(9.90))
    client.forecast("uk-spot-percentiles", "00350584")

    forecasts = [u for u in calls if "/locations/" in u]
    assert len(forecasts) == 1, "second call in the same hour should be cached"


def test_forecast_refetches_after_the_hour_turns(config, monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "/instances" in str(request.url) and "/locations" not in str(request.url):
            return httpx.Response(200, json={"instances": [{"id": "blended"}]})
        return httpx.Response(200, json={"type": "CoverageCollection", "coverages": []})

    client = make_client(config, handler)
    monkeypatch.setattr(time, "time", lambda: at(9.10))
    client.forecast("uk-spot-percentiles", "00350584")
    monkeypatch.setattr(time, "time", lambda: at(10.30))
    client.forecast("uk-spot-percentiles", "00350584")

    forecasts = [u for u in calls if "/locations/" in u]
    assert len(forecasts) == 2, "a new hour should refetch"


# --- Provenance -----------------------------------------------------------------
#
# The stale fallback above keeps the page useful during an outage. These cover the
# other half of that bargain: the caller must be able to tell that it happened.


def test_fresh_fetch_is_not_marked_stale(config):
    client = make_client(config, lambda r: httpx.Response(200, json={"good": True}))
    fetched = client.fetch("/x")
    assert fetched.payload == {"good": True}
    assert fetched.stale is False


def test_stale_fallback_is_marked_and_keeps_the_original_time(config, monkeypatch):
    """The age reported must be the data's, not the moment the retry failed."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    state = {"fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            return httpx.Response(500)
        return httpx.Response(200, json={"good": True})

    client = make_client(config, handler)
    monkeypatch.setattr(time, "time", lambda: at(9.0))
    client.fetch("/x")

    state["fail"] = True
    monkeypatch.setattr(time, "time", lambda: at(12.0))
    fetched = client.fetch("/x", ttl=0)

    assert fetched.payload == {"good": True}
    assert fetched.stale is True
    assert fetched.retrieved_at.hour == _local(at(9.0)).hour, "reported the retry, not the data"


def test_cache_hit_reports_when_the_data_was_stored(config, monkeypatch):
    """A cached page is not stale, but it is still older than now."""
    client = make_client(config, lambda r: httpx.Response(200, json={"good": True}))
    monkeypatch.setattr(time, "time", lambda: at(9.0))
    client.fetch("/x")
    monkeypatch.setattr(time, "time", lambda: at(9.2))
    fetched = client.fetch("/x", ttl=10**9)
    assert fetched.stale is False
    assert fetched.retrieved_at == _local(at(9.0))


def test_health_state_records_success(config):
    client = make_client(config, lambda r: httpx.Response(200, json={}))
    client.fetch("/x")
    assert client.last_success_at is not None
    assert client.last_failure_at is None


def test_health_state_records_failure(config, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    client = make_client(config, lambda r: httpx.Response(403))
    with pytest.raises(AuthError):
        client.fetch("/x")
    assert client.last_failure_at is not None
    assert "403" in client.last_failure


def test_health_state_records_failure_even_when_stale_data_rescues_the_page(config, monkeypatch):
    """The page recovers, but the operator still needs to know the API refused."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    state = {"fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403) if state["fail"] else httpx.Response(200, json={"good": True})

    client = make_client(config, handler)
    client.fetch("/x")
    state["fail"] = True
    assert client.fetch("/x", ttl=0).stale is True
    assert client.last_failure_at is not None
    assert client.last_success_at < client.last_failure_at
