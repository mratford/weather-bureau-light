"""Route and rendering tests, driven through a fake API client."""

from __future__ import annotations

import logging
import re
from dataclasses import replace


def text(response) -> str:
    return response.get_data(as_text=True)


def client_defaulting_to(default_site, config, fake_client, geocoder):
    """A test client with WBL_DEFAULT_SITE set to an id, a place name or nonsense."""
    from weather_bureau_light.app import create_app
    from weather_bureau_light.service import ForecastService

    configured = replace(config, default_site=default_site)
    service = ForecastService(configured, client=fake_client, geocoder=geocoder)
    app = create_app(config=configured, service=service)
    app.config["TESTING"] = True
    return app.test_client()


def test_index_redirects_to_default_site(client):
    response = client.get("/")
    assert response.status_code == 302
    # Brentwood is the nearest fixture site to the configured default coordinates.
    assert "/forecast/00350584" in response.headers["Location"]


def test_default_site_accepts_a_spot_site_id(config, fake_client, geocoder):
    client = client_defaulting_to("00000003", config, fake_client, geocoder)
    assert "/forecast/00000003" in client.get("/").headers["Location"]


def test_default_site_accepts_a_place_name(config, fake_client, geocoder):
    """A name is geocoded and mapped to its nearest site, as a search would be."""
    client = client_defaulting_to("London", config, fake_client, geocoder)
    assert "/forecast/00000003" in client.get("/").headers["Location"]


def test_default_site_place_name_beats_the_hardcoded_fallback(config, fake_client, geocoder):
    """Londonderry must not quietly come back as Brentwood."""
    client = client_defaulting_to("Londonderry", config, fake_client, geocoder)
    assert "/forecast/00000009" in client.get("/").headers["Location"]


def test_default_site_accepts_a_postcode(config, fake_client, geocoder, caplog):
    """CM14 4BX is in Brentwood, so check it resolved rather than merely fell back."""
    client = client_defaulting_to("CM14 4BX", config, fake_client, geocoder)
    with caplog.at_level(logging.WARNING, logger="weather_bureau_light.service"):
        response = client.get("/")
    assert "/forecast/00350584" in response.headers["Location"]
    assert not caplog.records, "postcode was not resolved"


def test_unresolvable_default_site_warns_and_falls_back(config, fake_client, geocoder, caplog):
    client = client_defaulting_to("Chelmsfrod", config, fake_client, geocoder)
    with caplog.at_level(logging.WARNING, logger="weather_bureau_light.service"):
        response = client.get("/")
    assert "/forecast/00350584" in response.headers["Location"]
    assert any("Chelmsfrod" in record.getMessage() for record in caplog.records)


def test_forecast_page_renders(client):
    response = client.get("/forecast/00350584")
    assert response.status_code == 200
    body = text(response)
    assert "Brentwood" in body
    assert "forecast site" in body or "Brentwood" in body


def test_all_expected_rows_present(client):
    body = text(client.get("/forecast/00350584"))
    for label in [
        "Time",
        "Weather",
        "Chance of precipitation",
        "Temperature",
        "Feels like",
        "Likely range",
        "Wind speed and direction",
        "Wind gust",
        "Visibility",
        "Humidity",
        "UV index",
        "Pressure",
    ]:
        assert f">{label}" in body or f"{label} <" in body, f"missing row: {label}"


def test_no_missing_field_warning_when_everything_resolves(client):
    body = text(client.get("/forecast/00350584"))
    assert "Not available from the API" not in body


def test_day_tabs_rendered(client):
    body = text(client.get("/forecast/00350584"))
    assert body.count("day-tab") >= 3
    assert "2026-08-15" in body


def test_selecting_a_day_marks_it(client):
    body = text(client.get("/forecast/00350584?date=2026-08-16"))
    selected = re.search(r'class="day-tab is-selected"[^>]*href="([^"]+)"', body)
    assert selected and "2026-08-16" in selected.group(1)


def test_unknown_date_falls_back_to_first_day(client):
    assert client.get("/forecast/00350584?date=1999-01-01").status_code == 200


def test_unknown_site_is_404(client):
    assert client.get("/forecast/nosuchsite").status_code == 404


def test_temperatures_render_as_celsius_not_kelvin(client):
    body = text(client.get("/forecast/00350584"))
    chips = re.findall(r'data-t="(-?\d+)"', body)
    assert chips, "no temperature chips rendered"
    assert all(-40 < int(v) < 50 for v in chips)


def test_cells_are_populated_not_all_dashes(client):
    body = text(client.get("/forecast/00350584"))
    row = re.search(r'<tr class="row-humidity">(.*?)</tr>', body, re.S)
    assert row and row.group(1).count("&mdash;") < 3


def test_weather_symbols_rendered(client):
    body = text(client.get("/forecast/00350584"))
    assert "wx-clear" in body or "wx-cloudy" in body
    assert "<use href=" in body


def test_sprite_defs_included_once(client):
    body = text(client.get("/forecast/00350584"))
    assert body.count('class="sprite-defs"') == 1


def test_search_lists_multiple_matches(client):
    body = text(client.get("/search?q=Lon"))
    assert "London" in body
    assert "Londonderry" in body


def test_search_maps_each_place_to_its_nearest_site(client):
    """'London' also matches Londonderry; each gets a different spot site."""
    body = text(client.get("/search?q=London"))
    names = re.findall(r"<strong>([^<]+)</strong>", body)
    assert names[0] == "London"
    assert "Londonderry" in names
    sites = re.findall(r"forecast site (\d+)", body)
    assert len(set(sites)) == len(sites), "two places collapsed onto one site"


def test_search_single_match_redirects(client):
    response = client.get("/search?q=Brentwood")
    assert response.status_code == 302
    assert "/forecast/00350584" in response.headers["Location"]


def test_search_no_match_is_graceful(client):
    response = client.get("/search?q=Atlantis")
    assert response.status_code == 200
    assert "No sites matched" in text(response)


def test_search_empty_query(client):
    assert client.get("/search?q=").status_code == 200


def test_site_catalogue_fetched_once_across_requests(client, fake_client):
    """The catalogue is large; it must not be refetched per page view."""
    client.get("/forecast/00350584")
    client.get("/forecast/00000003")
    assert len([c for c in fake_client.calls if c.startswith("locations:")]) == 1


def test_both_collections_are_queried(client, fake_client):
    client.get("/forecast/00350584")
    forecasts = [c for c in fake_client.calls if c.startswith("forecast:")]
    assert any("percentiles" in c for c in forecasts)
    assert any("probabilities" in c for c in forecasts)


def test_units_note_documents_visibility_bands(client):
    body = text(client.get("/forecast/00350584"))
    assert "VP" in body and "40km" in body


def test_required_met_office_attribution_is_shown(client):
    """Clause 2.6.1 of the DataHub terms asks for this wording by the visualisation."""
    assert "Data supplied by the Met Office" in text(client.get("/forecast/00350584"))


def test_page_is_dressed_for_the_season(client):
    from datetime import datetime

    from weather_bureau_light.config import UK_TZ
    from weather_bureau_light.season import palette_for

    today = palette_for(datetime.now(UK_TZ).date())
    assert f'class="season-{today}"' in text(client.get("/forecast/00350584"))


def test_every_season_has_a_masthead_palette(client):
    """A season with no rule would silently fall back to the autumn default."""
    css = text(client.get("/static/metoffice.css"))
    for name in ("autumn", "winter", "spring", "summer", "christmas", "halloween"):
        block = css[css.index(f".season-{name}") :][:400]
        for token in ("--brand:", "--brand-ink:", "--brand-edge:", "--action:"):
            assert token in block, f"{name} is missing {token}"


def test_halloween_turns_the_whole_page_dark(client, monkeypatch):
    """The one palette that overrides the content tokens, not just the brand ones."""
    monkeypatch.setattr("weather_bureau_light.app.palette_for", lambda day: "halloween")
    body = text(client.get("/forecast/00350584"))
    assert 'class="season-halloween"' in body
    assert "Weather Bureau" in body and ">Dark<" in body

    css = text(client.get("/static/metoffice.css"))
    block = css[css.index(".season-halloween") :][:400]
    for token in ("--page:", "--ink:", "--band:", "--rule:"):
        assert token in block, f"halloween is missing {token}"


def test_the_masthead_says_light_on_an_ordinary_day(client, monkeypatch):
    monkeypatch.setattr("weather_bureau_light.app.palette_for", lambda day: "summer")
    assert ">Light<" in text(client.get("/forecast/00350584"))


def test_row_headings_are_wrapped_for_the_mobile_layout(client):
    """On a phone the label is lifted out of the layout, which needs its own element:
    without the span there is nothing to position and the heading column returns."""
    body = text(client.get("/forecast/00350584"))
    assert body.count('<th scope="row"><span class="row-label">') == 12

    css = text(client.get("/static/metoffice.css"))
    mobile = css[css.index("@media (max-width: 700px)") :]
    assert "--row-label-w: 0" in mobile
    assert ".row-label" in mobile


def test_static_css_is_served(client):
    response = client.get("/static/metoffice.css")
    assert response.status_code == 200
    assert "forecast-table" in text(response)


def test_selected_day_is_scrolled_into_view(client):
    """The day strip scrolls horizontally and a page load resets it to the left,
    which would leave a later day off-screen behind the tabs that do fit."""
    body = text(client.get("/forecast/00350584?date=2026-08-16"))
    assert "day-tab is-selected" in body
    assert "scrollLeft" in body, "missing the script that reveals the selected day"


def test_scroll_containers_reserve_a_scrollbar_gutter(client):
    """Overlay scrollbars float over content, so both scrolling containers need
    clearance or the bar covers the pressure row and the sunrise/sunset line."""
    css = text(client.get("/static/metoffice.css"))
    assert "--scrollbar-gutter" in css
    for block in (".table-scroll", ".day-tabs"):
        start = css.index(block)
        assert "padding-bottom: var(--scrollbar-gutter)" in css[start : start + 400], block
