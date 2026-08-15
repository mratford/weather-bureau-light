"""Tests for the spot-site catalogue and geo lookup.

The live API publishes ids and coordinates with empty properties, so these fixtures
mirror that: sites are nameless and names arrive later from the geocoder.
"""

from __future__ import annotations

import pytest

from bpf_fixtures import LOCATIONS
from weather_bureau_light.sites import Site, SiteCatalogue, haversine_km, parse_locations


@pytest.fixture
def catalogue() -> SiteCatalogue:
    return SiteCatalogue(parse_locations(LOCATIONS))


def test_parses_all_features(catalogue):
    assert len(catalogue) == 3
    site = catalogue.get("00350584")
    assert site.latitude == pytest.approx(51.62)
    assert site.longitude == pytest.approx(0.3088)
    assert site.elevation == pytest.approx(104.0)


def test_sites_have_no_name_from_the_api(catalogue):
    """The forecast service supplies coordinates only."""
    assert catalogue.get("00350584").name is None


def test_display_name_falls_back_to_the_id(catalogue):
    assert catalogue.get("00350584").display_name == "Site 00350584"


def test_named_attaches_a_label(catalogue):
    named = catalogue.get("00350584").named("Brentwood", "Essex")
    assert named.display_name == "Brentwood (Essex)"
    assert named.id == "00350584"
    # The original is left untouched.
    assert catalogue.get("00350584").name is None


def test_named_without_region():
    assert Site("1", 0.0, 0.0).named("Nowhere").display_name == "Nowhere"


def test_skips_features_without_coordinates():
    payload = {"features": [{"id": "x", "geometry": {}, "properties": {}}]}
    assert parse_locations(payload) == []


def test_honours_a_name_if_the_api_ever_supplies_one():
    payload = {
        "features": [
            {
                "id": "z",
                "geometry": {"type": "Point", "coordinates": [0.0, 51.0]},
                "properties": {"name": "Somewhere", "region": "Kent"},
            }
        ]
    }
    site = parse_locations(payload)[0]
    assert site.display_name == "Somewhere (Kent)"


def test_nearest_site(catalogue):
    assert catalogue.nearest(51.62, 0.3088).id == "00350584"
    assert catalogue.nearest(51.5072, -0.1276).id == "00000003"
    assert catalogue.nearest(54.99, -7.31).id == "00000009"


def test_nearest_on_empty_catalogue():
    assert SiteCatalogue([]).nearest(51.6, 0.3) is None


def test_search_finds_nothing_when_sites_are_nameless(catalogue):
    """Name search has to go through the geocoder instead."""
    assert catalogue.search("Brentwood") == []


def test_haversine_known_distance():
    # Brentwood to central London is about 30 km.
    assert haversine_km(51.6214, 0.3053, 51.5072, -0.1276) == pytest.approx(33, abs=4)


def test_haversine_zero_distance():
    assert haversine_km(51.6, 0.3, 51.6, 0.3) == pytest.approx(0.0)
