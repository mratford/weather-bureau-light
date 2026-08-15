"""Dump the live shape of the Met Office BPF API so the parameter mapping can be
written against reality rather than guessed.

The public docs never enumerate the parameter names - the glossary loads over
JavaScript and the API rejects unauthenticated requests - so they direct you to read
the collection endpoint instead. This does that, and saves everything it sees.

    uv run python scripts/discover.py

Writes to scratch/discovery/. Costs a handful of API calls.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from weather_bureau_light.config import (  # noqa: E402
    BASE_URL_V1,
    BASE_URL_V2,
    DEFAULT_LATITUDE,
    DEFAULT_LONGITUDE,
    PROJECT_ROOT,
    Config,
    ConfigError,
)

OUT_DIR = PROJECT_ROOT / "scratch" / "discovery"


def save(name: str, payload: object) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def get(client: httpx.Client, base: str, path: str, **params: str) -> object | None:
    url = f"{base}{path}"
    try:
        response = client.get(url, params=params or None)
    except httpx.HTTPError as exc:
        print(f"  ! {path}: {exc}")
        return None
    if response.status_code != 200:
        print(f"  ! {path}: HTTP {response.status_code} {response.text[:200]}")
        return None
    print(f"  . {path}: ok ({len(response.content)} bytes)")
    return response.json()


def pick_base_url(client: httpx.Client, configured: str) -> str | None:
    """Find a service version this key is actually subscribed to."""
    for base in dict.fromkeys([configured, BASE_URL_V2, BASE_URL_V1]):
        print(f"Trying {base}")
        if get(client, base, "/collections") is not None:
            return base
    return None


def summarise_parameters(doc: dict) -> None:
    """Print the parameter names, which is the whole point of running this."""
    params = doc.get("parameters") or {}
    if not params:
        return
    print(f"\n  {len(params)} parameters:")
    for name, meta in sorted(params.items()):
        unit = ""
        if isinstance(meta, dict):
            unit_doc = meta.get("unit") or {}
            unit = unit_doc.get("symbol") or unit_doc.get("label") or ""
            if isinstance(unit, dict):
                unit = unit.get("value", "")
        print(f"    {name}  [{unit}]")


def nearest_site(features: list[dict], lat: float, lon: float) -> dict | None:
    """Straight-line nearest site. Good enough to grab one sample response."""
    best, best_d = None, math.inf
    for feature in features:
        coords = (feature.get("geometry") or {}).get("coordinates")
        if not coords or len(coords) < 2:
            continue
        d = math.hypot(coords[0] - lon, coords[1] - lat)
        if d < best_d:
            best, best_d = feature, d
    return best


def main() -> int:
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"ERROR: {exc}")
        return 1

    with httpx.Client(
        headers={"apikey": config.api_key, "accept": "application/json"},
        timeout=30.0,
        follow_redirects=True,
    ) as client:
        base = pick_base_url(client, config.base_url)
        if base is None:
            print("\nERROR: no service version accepted this key.")
            return 1
        print(f"\nUsing {base}\n")

        collections = get(client, base, "/collections")
        save("collections", collections)

        ids = [
            c["id"]
            for c in (collections or {}).get("collections", [])
            if isinstance(c, dict) and "id" in c
        ]
        print(f"\nCollections: {ids or '(none listed)'}\n")

        for collection_id in ids:
            print(f"--- {collection_id} ---")

            instances = get(client, base, f"/collections/{collection_id}/instances")
            if instances is not None:
                save(f"instances_{collection_id}", instances)

            # Parameter definitions live on the collection entry itself.
            entry = next(
                (c for c in (collections or {}).get("collections", []) if c.get("id") == collection_id),
                {},
            )
            summarise_parameters(entry)

            if not collection_id.startswith("uk-"):
                print()
                continue

            # Locations hang off an instance, not the collection directly.
            instance_ids = [
                i["id"] for i in (instances or {}).get("instances", []) if isinstance(i, dict)
            ]
            if not instance_ids:
                print("  ! no instances, cannot fetch locations\n")
                continue
            instance = instance_ids[-1]
            print(f"  instance: {instance}")
            prefix = f"/collections/{collection_id}/instances/{instance}"

            locations = get(client, base, f"{prefix}/locations")
            if locations is None:
                print()
                continue
            features = locations.get("features", [])
            print(f"  {len(features)} locations")
            # The full catalogue is large; keep a sample plus the one we query.
            save(f"locations_sample_{collection_id}", {**locations, "features": features[:25]})

            site = nearest_site(features, DEFAULT_LATITUDE, DEFAULT_LONGITUDE)
            if site is None:
                print()
                continue
            site_id = site.get("id") or (site.get("properties") or {}).get("id")
            print(f"  nearest site to Brentwood: {site_id} {site.get('properties', {})}")
            save(f"site_{collection_id}", site)

            data = get(client, base, f"{prefix}/locations/{site_id}")
            if data is not None:
                save(f"forecast_{collection_id}", data)
                summarise_parameters(data)
                axes = ((data.get("domain") or {}).get("axes") or {})
                print(f"\n  domain axes: {list(axes)}")
                for axis_name, axis in axes.items():
                    values = axis.get("values")
                    if isinstance(values, list):
                        preview = values[:3]
                        print(f"    {axis_name}: {len(values)} values, e.g. {preview}")
                ranges = data.get("ranges") or {}
                for param_name, rng in list(ranges.items())[:3]:
                    print(
                        f"    range {param_name}: axisNames={rng.get('axisNames')} "
                        f"shape={rng.get('shape')} n={len(rng.get('values') or [])}"
                    )
            print()

    print(f"Saved to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
