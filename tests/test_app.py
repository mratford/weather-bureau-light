"""Route and rendering tests using a fake API client."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from dataclasses import replace

from weather_bureau_light.config import UK_TZ
from weather_bureau_light.warnings import Warning


def text(response) -> str:
    return response.get_data(as_text=True)


def client_defaulting_to(default_site, config, fake_client, geocoder):
    """Create a test client with an id, place name, or invalid default site."""
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
    # Brentwood is nearest to the configured fixture coordinates.
    assert "/forecast/00350584" in response.headers["Location"]


def test_default_site_accepts_a_spot_site_id(config, fake_client, geocoder):
    client = client_defaulting_to("00000003", config, fake_client, geocoder)
    assert "/forecast/00000003" in client.get("/").headers["Location"]


def test_default_site_accepts_a_place_name(config, fake_client, geocoder):
    """A name is geocoded and mapped to its nearest site."""
    client = client_defaulting_to("London", config, fake_client, geocoder)
    assert "/forecast/00000003" in client.get("/").headers["Location"]


def test_default_site_place_name_beats_the_hardcoded_fallback(config, fake_client, geocoder):
    """Londonderry should not resolve to Brentwood."""
    client = client_defaulting_to("Londonderry", config, fake_client, geocoder)
    assert "/forecast/00000009" in client.get("/").headers["Location"]


def test_default_site_accepts_a_postcode(config, fake_client, geocoder, caplog):
    """CM14 4BX resolves to Brentwood rather than the default site."""
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


def test_weather_warning_for_current_site_is_rendered(client, fake_warnings):
    fake_warnings.warnings.append(
        Warning(
            weather_types=("RAIN",),
            level="YELLOW",
            headline="Heavy rain may cause some disruption",
            valid_from=datetime(2026, 8, 15, 12, tzinfo=UK_TZ),
            valid_to=datetime(2026, 8, 15, 20, tzinfo=UK_TZ),
            further_details="Rain is expected to be persistent.",
            what_to_expect=("Some surface water is possible.",),
            what_should_i_do="Take care when travelling.",
        )
    )
    body = text(client.get("/forecast/00350584"))
    assert "Met Office Weather Warnings" in body
    assert "Heavy rain may cause some disruption" in body
    assert "What to expect" in body


def test_no_weather_warning_block_when_current_site_has_none(client):
    body = text(client.get("/forecast/00350584"))
    assert "Met Office Weather Warnings" not in body


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
    """'London' also matches Londonderry, and the results use different sites."""
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
    """The large catalogue is fetched once and reused across page views."""
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
    """The required attribution appears beside the visualisation."""
    assert "Data supplied by the Met Office" in text(client.get("/forecast/00350584"))


def test_page_is_dressed_for_the_season(client):
    body = text(client.get("/forecast/00350584"))
    assert re.search(r'class="season-(autumn|winter|spring|summer|christmas|halloween)"', body)


def test_every_season_has_a_masthead_palette(client):
    """A season without a rule should not silently use the autumn default."""
    css = text(client.get("/static/metoffice.css"))
    for name in ("autumn", "winter", "spring", "summer", "christmas", "halloween"):
        block = css[css.index(f".season-{name}") :][:400]
        for token in ("--brand:", "--brand-ink:", "--brand-edge:", "--action:"):
            assert token in block, f"{name} is missing {token}"


def test_ordinary_season_mastheads_are_black_on_white(client):
    css = text(client.get("/static/metoffice.css"))
    for name in ("autumn", "winter", "spring", "summer"):
        block = css[css.index(f".season-{name}") :][:400]
        assert "--brand: #ffffff" in block
        assert "--brand-ink: #000000" in block
        assert "--brand-edge: #000000" in block
        assert "--action: #ffffff" in block
        assert "--action-ink: #000000" in block


def test_holiday_masthead_palettes_remain_special(client):
    css = text(client.get("/static/metoffice.css"))
    christmas = css[css.index(".season-christmas") :][:400]
    halloween = css[css.index(".season-halloween") :][:400]
    assert "--brand: #b3121f" in christmas
    assert "--action: #146b3a" in christmas
    assert "--brand: #0a0a0a" in halloween
    assert "--action: #8b0000" in halloween


def test_masthead_has_a_thin_black_border(client):
    css = text(client.get("/static/metoffice.css"))
    masthead = css[css.index(".masthead {\n  background") :][:220]
    assert "border: 1px solid #000000" in masthead


def test_halloween_turns_the_whole_page_dark(client, monkeypatch):
    """This palette overrides content tokens as well as brand tokens."""
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
    """On a phone the label needs a separate element for positioning; otherwise the
    heading column returns."""
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
    """The day strip restores the selected day after a page load resets its scroll."""
    body = text(client.get("/forecast/00350584?date=2026-08-16"))
    assert "day-tab is-selected" in body
    assert "scrollLeft" in body, "missing the script that reveals the selected day"


def test_scroll_containers_reserve_a_scrollbar_gutter(client):
    """Both scrolling containers reserve space so overlay scrollbars do not cover
    the pressure row or sunrise and sunset."""
    css = text(client.get("/static/metoffice.css"))
    assert "--scrollbar-gutter" in css
    for block in (".table-scroll", ".day-tabs"):
        start = css.index(block)
        assert "padding-bottom: var(--scrollbar-gutter)" in css[start : start + 400], block


# --- Stale data and health -----------------------------------------------------


def test_no_staleness_banner_when_the_data_is_fresh(client):
    assert "Live update failed" not in text(client.get("/forecast/00350584"))


def test_staleness_banner_names_the_age_when_the_api_is_down(client, fake_client):
    """A cached page must be distinguishable from a live page."""
    fake_client.serving_stale(age_hours=3)
    body = text(client.get("/forecast/00350584"))
    assert "Live update failed" in body
    assert "3 hours ago" in body
    # The forecast still renders while the page identifies the data as stale.
    assert "forecast-table" in body


def test_stale_page_reports_the_data_time_not_the_clock(client, fake_client):
    """'Updated:' reports retrieval time rather than page-render time."""
    from datetime import datetime, timedelta

    from weather_bureau_light.config import UK_TZ

    fake_client.serving_stale(age_hours=3)
    body = text(client.get("/forecast/00350584"))
    issued = (datetime.now(UK_TZ) - timedelta(hours=3)).strftime("%H:%M")
    assert f"Updated: {issued}" in body
    assert f"Updated: {datetime.now(UK_TZ).strftime('%H:%M')}" not in body


def test_healthz_is_ok_when_the_api_is_answering(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["last_failure"] is None
    assert body["sites_loaded"] > 0


def test_healthz_reports_degraded_with_503_after_a_failure(client, fake_client):
    """Return 503 so curl -f and uptime monitors detect the failure."""
    fake_client.serving_stale(age_hours=3, message="HTTP 403 from …")
    response = client.get("/healthz")
    assert response.status_code == 503
    body = response.get_json()
    assert body["status"] == "degraded"
    assert "403" in body["last_failure_message"]


def test_healthz_never_leaks_the_api_key(client, fake_client, config):
    """It is available on the LAN when WBL_HOST is 0.0.0.0."""
    fake_client.serving_stale(age_hours=1, message="denied")
    for response in (client.get("/healthz"), client.get("/healthz")):
        assert config.api_key not in text(response)


def test_healthz_spends_no_api_calls(client, fake_client):
    """Polling the health endpoint must not consume the daily quota."""
    client.get("/forecast/00350584")
    before = len(fake_client.calls)
    for _ in range(5):
        client.get("/healthz")
    assert fake_client.calls[before:] == []


# --- Icon assets ----------------------------------------------------------------


def test_home_screen_icon_is_linked_and_served(client):
    """iOS uses apple-touch-icon for a home-screen bookmark."""
    assert 'rel="apple-touch-icon"' in text(client.get("/forecast/00350584"))
    response = client.get("/static/apple-touch-icon.png")
    assert response.status_code == 200
    assert response.data.startswith(b"\x89PNG\r\n\x1a\n")


def test_home_screen_icon_is_the_size_ios_asks_for(client):
    """iOS scales a differently sized icon instead of using the intended asset."""
    import struct

    data = client.get("/static/apple-touch-icon.png").data
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (180, 180)


def test_home_screen_icon_is_opaque(client):
    """iOS fills transparent areas of the icon with black."""
    import struct

    data = client.get("/static/apple-touch-icon.png").data
    colour_type = struct.unpack(">B", data[25:26])[0]
    assert colour_type == 2, "expected truecolour without an alpha channel"


def test_favicon_and_manifest_are_served(client):
    assert client.get("/static/favicon-32.png").status_code == 200
    response = client.get("/static/site.webmanifest")
    assert response.status_code == 200
    assert "icon-512.png" in text(response)


def test_manifest_icons_all_exist(client):
    """A manifest should list only icons that exist."""
    import json

    manifest = json.loads(text(client.get("/static/site.webmanifest")))
    for icon in manifest["icons"]:
        assert client.get(f"/static/{icon['src']}").status_code == 200, icon["src"]


# --- Elapsed forecast hours -----------------------------------------------------


def _at(client, monkeypatch, hour, minute=0):
    """Render today's table for a specified time."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    monkeypatch.setattr(
        "weather_bureau_light.model._now",
        lambda tz: datetime(2026, 8, 15, hour, minute, tzinfo=ZoneInfo("Europe/London")),
    )
    body = text(client.get("/forecast/00350584"))
    row = re.search(r'<tr class="row-time">(.*?)</tr>', body, re.S)
    assert row, "no time row rendered"
    return body, re.findall(r"<td>([^<]+)</td>", row.group(1))


def test_table_starts_at_the_current_hour(client, monkeypatch):
    _, hours = _at(client, monkeypatch, 17, 49)
    assert hours[0] == "17:00", hours[:4]


def test_table_does_not_show_hours_that_have_passed(client, monkeypatch):
    _, hours = _at(client, monkeypatch, 17, 49)
    for gone in ("04:00", "09:00", "16:00"):
        assert gone not in hours, f"{gone} is in the past"


def test_the_day_high_and_low_still_cover_the_whole_day(client, monkeypatch):
    """The day tab summarises the full day and must not shrink as hours pass."""
    morning, _ = _at(client, monkeypatch, 4)
    evening, _ = _at(client, monkeypatch, 21)

    def first_tab(body):
        return re.search(r'<a class="day-tab is-selected".*?</a>', body, re.S).group(0)

    def temps(tab):
        return re.findall(r'class="t-(?:max|min)">(-?\d+)&deg;', tab)

    assert temps(first_tab(evening)) == temps(first_tab(morning))
