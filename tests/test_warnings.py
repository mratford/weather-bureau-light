"""Tests for fetching and filtering NSWWS warnings."""

from __future__ import annotations

from dataclasses import replace

import httpx

from weather_bureau_light.warnings import WarningsClient, _point_in_geometry


ISSUED_URL = "https://example.invalid/nswws/v1.1/objects/issued/current"
FEED = f'''<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <link rel="related" href="{ISSUED_URL}" />
</feed>'''


def warning_document():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [[[
                        [-1, 51], [1, 51], [1, 52], [-1, 52], [-1, 51]
                    ]]],
                },
                "properties": {
                    "warningStatus": "ISSUED",
                    "warningLevel": "YELLOW",
                    "weatherType": ["RAIN"],
                    "warningHeadline": "Heavy rain may cause disruption",
                    "validFromDate": "2026-09-09T12:00:00Z",
                    "validToDate": "2026-09-09T20:00:00Z",
                    "warningFurtherDetails": "Persistent rain is expected.",
                    "whatToExpect": ["Some surface water."],
                    "whatShouldIDo": "Take care when travelling.",
                },
            }
        ],
    }


def test_warning_client_uses_apikey_and_selects_site(config):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["apikey"] = request.headers.get("apikey")
        if request.url.path.endswith("/objects/feed"):
            return httpx.Response(200, text=FEED)
        return httpx.Response(200, json=warning_document())

    config = replace(
        config, nswws_api_key="test-key", nswws_base_url="https://example.invalid/nswws/v1.1"
    )
    client = WarningsClient(
        config,
        client=httpx.Client(transport=httpx.MockTransport(handler), headers={"apikey": "test-key"}),
    )
    warnings = client.for_site(51.62, 0.3088)
    assert seen["apikey"] == "test-key"
    assert len(warnings) == 1
    assert warnings[0].headline == "Heavy rain may cause disruption"
    assert warnings[0].weather_label == "Rain"


def test_warning_outside_current_site_is_not_rendered(config):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/feed"):
            return httpx.Response(200, text=FEED)
        return httpx.Response(200, json=warning_document())

    config = replace(
        config, nswws_api_key="test-key", nswws_base_url="https://example.invalid/nswws/v1.1"
    )
    client = WarningsClient(
        config,
        client=httpx.Client(transport=httpx.MockTransport(handler), headers={"apikey": "test-key"}),
    )
    assert client.for_site(55.0, 0.0) == []


def test_no_key_makes_no_network_request(config):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("warnings API should be disabled without its key")

    client = WarningsClient(config, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.for_site(51.62, 0.3088) == []


def test_point_in_multipolygon_respects_holes():
    geometry = {
        "type": "MultiPolygon",
        "coordinates": [[
            [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
            [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]],
        ]],
    }
    assert _point_in_geometry(2, 2, geometry)
    assert not _point_in_geometry(5, 5, geometry)
