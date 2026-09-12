"""Connect the client, geocoder, parameter resolution, and model assembly."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import covjson, model, parameters
from .config import (
    DEFAULT_LATITUDE,
    DEFAULT_LONGITUDE,
    PERCENTILES_COLLECTION,
    PROBABILITIES_COLLECTION,
    UK_TZ,
    Config,
)
from .datahub import DataHubClient, DataHubError, DiskCache
from .geocode import Geocoder, Place
from .parameters import PERCENTILE_FIELDS, PROBABILITY_FIELDS
from .sites import Site, SiteCatalogue, haversine_km
from .warnings import Warning, WarningsClient, WarningsError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchHit:
    """A searched place paired with the spot site that serves it."""

    place: Place
    site: Site
    distance_km: float


class ForecastService:
    def __init__(
        self,
        config: Config,
        client: DataHubClient | None = None,
        geocoder: Geocoder | None = None,
        warnings_client: WarningsClient | None = None,
    ) -> None:
        self.config = config
        self.client = client or DataHubClient(config)
        self.geocoder = geocoder or Geocoder(DiskCache(config.cache_dir))
        # Test and offline clients should not make unexpected live warning requests.
        self.warnings_client = warnings_client or (
            WarningsClient(config) if client is None else None
        )
        self._catalogue: SiteCatalogue | None = None
        self._resolutions: dict[str, parameters.Resolution] = {}

    def close(self) -> None:
        self.client.close()
        self.geocoder.close()
        if self.warnings_client is not None:
            self.warnings_client.close()

    def warnings(self, site: Site) -> list[Warning]:
        """Return current warnings whose impact polygon contains the selected site."""
        if self.warnings_client is None:
            return []
        try:
            return self.warnings_client.for_site(site.latitude, site.longitude)
        except WarningsError as exc:
            # A warning outage should not prevent the forecast page from loading.
            # Returning no warnings is safer than displaying an outdated warning.
            log.warning("weather warnings unavailable: %s", exc)
            return []

    @property
    def catalogue(self) -> SiteCatalogue:
        if self._catalogue is None:
            self.client.ensure_base_url()
            self._catalogue = SiteCatalogue.load(self.client, PERCENTILES_COLLECTION)
            log.info("loaded %d spot sites", len(self._catalogue))
        return self._catalogue

    def name_site(self, site: Site) -> Site:
        """Attach a human-readable name, which the forecast API does not provide."""
        if site.name:
            return site
        place = self.geocoder.reverse(site.latitude, site.longitude)
        return site.named(place.name, place.region) if place else site

    def site(self, site_id: str) -> Site | None:
        site = self.catalogue.get(site_id)
        return None if site is None else self.name_site(site)

    def default_site(self) -> Site | None:
        """Resolve WBL_DEFAULT_SITE as a spot-site id or place name.

        Try the catalogue first so known ids continue to work; geocode other values.
        """
        configured = self.config.default_site
        if configured:
            site = self.catalogue.get(configured)
            if site is None:
                hits = self.search(configured, limit=1)
                if hits:
                    site = hits[0].site
                    log.info("default site %r resolved to %s", configured, site.display_name)
            if site is not None:
                return self.name_site(site)
            # Explain an invalid location instead of silently using the default.
            log.warning("WBL_DEFAULT_SITE=%r matched no site, falling back", configured)
        nearest = self.catalogue.nearest(DEFAULT_LATITUDE, DEFAULT_LONGITUDE)
        return None if nearest is None else self.name_site(nearest)

    def search(self, query: str, limit: int = 10) -> list[SearchHit]:
        """Geocode a query and map each result to its nearest spot site."""
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for place in self.geocoder.search(query, limit=limit):
            site = self.catalogue.nearest(place.latitude, place.longitude)
            if site is None or site.id in seen:
                continue
            seen.add(site.id)
            hits.append(
                SearchHit(
                    place=place,
                    site=site.named(place.name, place.region),
                    distance_km=haversine_km(
                        place.latitude, place.longitude, site.latitude, site.longitude
                    ),
                )
            )
        return hits

    def _resolution(self, collection_id: str, specs) -> parameters.Resolution:
        """Match the table fields against the collection's parameter names."""
        if collection_id in self._resolutions:
            return self._resolutions[collection_id]

        entry = self.client.collection(collection_id) or {}
        available = list((entry.get("parameter_names") or entry.get("parameters") or {}).keys())
        resolution = parameters.resolve(available, specs)
        if resolution.missing:
            log.warning(
                "%s: could not resolve %s from %d parameters",
                collection_id,
                resolution.missing,
                len(available),
            )
        self._resolutions[collection_id] = resolution
        return resolution

    def _load(self, collection_id: str, site: Site, specs):
        resolution = self._resolution(collection_id, specs)
        wanted = resolution.all_names()
        # Request only fields shown in the table; most of the collection's parameters
        # are not rendered.
        fetched = self.client.forecast(collection_id, site.id, wanted or None)
        coverages = covjson.parse_collection(fetched.payload)

        if resolution.missing and coverages.parameter_names:
            retry = parameters.resolve(coverages.parameter_names, specs)
            if len(retry.missing) < len(resolution.missing):
                log.info("%s: re-resolved parameters from the data response", collection_id)
                resolution = self._resolutions[collection_id] = retry
        return coverages, resolution, fetched

    def forecast(self, site: Site) -> model.Forecast:
        percentiles, percentile_resolution, percentile_fetch = self._load(
            PERCENTILES_COLLECTION, site, PERCENTILE_FIELDS
        )
        fetches = [percentile_fetch]

        probabilities = None
        probability_resolution = None
        try:
            probabilities, probability_resolution, probability_fetch = self._load(
                PROBABILITIES_COLLECTION, site, PROBABILITY_FIELDS
            )
            fetches.append(probability_fetch)
        except (DataHubError, covjson.CovJsonError) as exc:
            # The forecast page can still be used without the precipitation row.
            log.warning("probabilities unavailable, precipitation row will be blank: %s", exc)

        # Use the older retrieval time so the page is not fresher than either collection.
        return model.build(
            site=site,
            percentiles=percentiles,
            percentile_resolution=percentile_resolution,
            probabilities=probabilities,
            probability_resolution=probability_resolution,
            tz=UK_TZ,
            issued=min(f.retrieved_at for f in fetches),
            stale=any(f.stale for f in fetches),
        )
