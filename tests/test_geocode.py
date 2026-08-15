"""Tests for postcodes.io geocoding, needed because BPF sites carry no names."""

from __future__ import annotations

import httpx
import pytest

from conftest import geocoder_handler
from weather_bureau_light.datahub import DiskCache
from weather_bureau_light.geocode import POSTCODE_RE, Geocoder


def make(cache_dir, handler=geocoder_handler) -> Geocoder:
    return Geocoder(
        DiskCache(cache_dir),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_place_search(geocoder):
    results = geocoder.search("Brentwood")
    assert results[0].name == "Brentwood"
    assert results[0].latitude == pytest.approx(51.6214)
    assert results[0].region == "Essex"


def test_place_search_no_match(geocoder):
    assert geocoder.search("Atlantis") == []


def test_empty_query_makes_no_request(tmp_path):
    def explode(request):
        raise AssertionError("should not have called the geocoder")

    assert make(tmp_path, explode).search("   ") == []


@pytest.mark.parametrize("code", ["CM14 4BX", "cm144bx", "SW1A 1AA", "M1 1AE"])
def test_postcode_pattern_matches(code):
    assert POSTCODE_RE.match(code)


@pytest.mark.parametrize("text", ["Brentwood", "London", "12345"])
def test_postcode_pattern_rejects_place_names(text):
    assert not POSTCODE_RE.match(text)


def test_postcode_lookup(geocoder):
    results = geocoder.search("CM14 4BX")
    assert results[0].kind == "postcode"
    assert results[0].latitude == pytest.approx(51.6198)
    assert results[0].name == "CM14 4BX"


def test_postcode_lookup_ignores_spacing(geocoder):
    assert geocoder.search("cm144bx")[0].latitude == pytest.approx(51.6198)


def test_reverse_geocoding_names_a_site(geocoder):
    place = geocoder.reverse(51.62, 0.3088)
    assert place.name == "Brentwood"
    assert place.region == "East of England"


def test_results_are_cached(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return geocoder_handler(request)

    geocoder = make(tmp_path, handler)
    geocoder.search("Brentwood")
    geocoder.search("Brentwood")
    assert len(calls) == 1


def test_unreachable_geocoder_degrades_quietly(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    geocoder = make(tmp_path, handler)
    assert geocoder.search("Brentwood") == []
    assert geocoder.reverse(51.6, 0.3) is None


def test_server_error_degrades_quietly(tmp_path):
    geocoder = make(tmp_path, lambda r: httpx.Response(500))
    assert geocoder.search("Brentwood") == []


def test_404_is_treated_as_no_result(tmp_path):
    geocoder = make(tmp_path, lambda r: httpx.Response(404, json={"result": None}))
    assert geocoder.search("ZZ1 1ZZ") == []


def test_search_falls_back_to_places_for_unmatched_postcode(geocoder):
    """A postcode-shaped string with no postcode hit still tries a place search."""
    assert geocoder.search("ZZ1 1ZZ") == []
