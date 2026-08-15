**Warning** This is completely AI generated, with all that that implies.

# weather-bureau-light

A locally hosted website recreating the old Met Office design.

The new Met Office website is very pretty but light text on a dark background
is difficult to read for some of us with astigmatism and there's less
information on a single page. This repo creates a website that uses the Met 
Office API to display dense forecast data on a light background. 

There is a bonus "likely range" row which is the 10th–90th percentile spread
for the temperature, and extra days.

![The forecast page for Brentwood: a scrollable strip of day tabs, each with a weather symbol, max and min temperature and sunrise and sunset times, above an hourly table whose rows are weather symbol, chance of precipitation, temperature, feels like, likely range, wind speed and direction, wind gust, visibility, humidity, UV index and pressure.](docs/screenshot.jpg)

## Setup

Open a [Met Office data](https://datahub.metoffice.gov.uk/) account and subscribe to
the Site-Specific Blended Probabilistic Forecast API. This is free for up
to 55 calls per day, which should be fine for personal use.

[Install uv](https://docs.astral.sh/uv/getting-started/installation/).

Create a `.env` file from the example
```sh
cp .env.example .env
```
and edit it to include your API key.

Install the necessary Python packages with 
```sh
uv sync
```

Then to run
```sh
uv run weather-bureau-light
```
and open <http://127.0.0.1:5000/>.

The default location is Brentwood (Essex). Use the search box for anywhere else, or
set `WBL_DEFAULT_SITE` to a spot-site id.

## Development notes 
### Notes on the API

Things worth knowing, all confirmed against the live service by `scripts/discover.py`:

- **Collection ids are `uk-spot-percentiles` / `uk-spot-probabilities`.** The Met
  Office's own published sample client still uses `improver-*-spot-uk`, which no longer
  exists.
- **Data hangs off an instance**: `/collections/{id}/instances/blended/locations/{siteId}`.
- **Auth is an `apikey` request header** — not Bearer, not a query parameter.
- **Two collections are needed.** Percentiles carry temperature, wind, humidity,
  visibility, UV, pressure and the weather symbol. Chance of precipitation is a
  probability and lives in the probabilities collection, against a threshold axis; the
  0.1 mm/hr threshold is the one the Met Office means by "chance of precipitation".
- **The response is a `CoverageCollection`** — one coverage per parameter, each with its
  own time axis, so parameters must be merged on timestamp rather than by position.
- **The service serialises values column-major while declaring `axisNames` row-major**,
  contrary to the CoverageJSON spec. Reading them as declared gives a 10th percentile
  above the 90th and a temperature curve with jumps — wrong, but plausible enough to
  ship unnoticed. `covjson.choose_order` detects the real layout from the data, using
  the fact that percentiles must increase and exceedance probabilities must decrease.
- **Locations have no names**, only ids and coordinates (8,667 of them). Place search
  therefore goes through [postcodes.io](https://postcodes.io), which handles both place
  names and postcodes; site names on the page come from reverse geocoding.
- **Parameter names are not documented publicly** — the glossary is JavaScript-rendered
  and the API rejects unauthenticated requests. `parameters.py` matches them by pattern
  against whatever the collection reports, so a rename degrades one row instead of
  breaking the page.
- Some parameters are published hourly (`Pt01h`) for the first days and three-hourly
  (`Pt03h`) after, so both are fetched and the finer one wins. Weather symbols stop at
  about day eight even though temperature runs to fourteen; those cells show a dash.

### Development

```sh
uv run pytest                     # 154 tests, no network required
uv run python scripts/discover.py # dump the live API shape to scratch/discovery/
```

Responses are cached under `.cache/` because the free tier is capped per day. Forecast
and instance requests **expire when the wall-clock hour turns** rather than after a
fixed interval: the data rolls hourly, its time axis advancing a step so the leading
columns drop off. Aligning to that boundary keeps already-past hours off the table and
caps refetches at 24 a day per site, against the 96 a 15-minute TTL would cost. A
five-minute floor stops a fetch at 09:59 expiring a minute later. The site catalogue is
cached for a week, and stale cache is served if the API fails, on the grounds that a
slightly old forecast beats an error page.

The cache records its own write time inside each file rather than trusting the mtime,
which a copy or a backup restore would silently reset.

## Attribution

Forecast data © Crown copyright, Met Office. The weather symbols are drawn for this
project — the Met Office's own artwork is Crown copyright and is not used.
