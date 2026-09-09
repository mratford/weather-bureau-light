**WARNING** This is completely AI generated, with all that that implies.

# weather-bureau-light

The new Met Office website is very pretty but light text on a dark background
is difficult to read for some of us with astigmatism and there's less
information on a single page. This repo creates a local website that uses the Met
Office API to display dense forecast data on a light background.

There is a bonus "likely range" row which is the 10th–90th percentile spread
for the temperature.

Please note that the [Met Office terms of service][terms] prohibit this being run as a
public instance.

![The forecast page for Brentwood: a scrollable strip of day tabs, each with a weather symbol, max and min temperature and sunrise and sunset times, above an hourly table whose rows are weather symbol, chance of precipitation, temperature, feels like, likely range, wind speed and direction, wind gust, visibility, humidity, UV index and pressure.](docs/screenshot.jpg)

## Setup

Open a [Met Office data account](https://datahub.metoffice.gov.uk/) and subscribe to
the Site-Specific Blended Probabilistic Forecast API. This is free for up
to 55 calls per day, which should be fine for personal use.

[Install uv](https://docs.astral.sh/uv/getting-started/installation/).

Create a `.env` file from the example
```sh
cp .env.example .env
```
and edit it to include your API key.

To show Met Office Weather Warnings for the selected site, also set
`METOFFICE_NSWWS_API_KEY` with a key for the NSWWS Public API. The warnings feed is
optional; without it the forecast page is unchanged.

Install the necessary Python packages with
```sh
uv sync
```

Then to run
```sh
uv run weather-bureau-light
```
and open <http://127.0.0.1:5000/>.

Set a default location with
`WBL_DEFAULT_SITE` in `.env`, which takes either a
place name or postcode
```
WBL_DEFAULT_SITE=Chelmsford
```
You can also use a spot-site id, which is the Met Office's own identifier for one of the fixed points
it forecasts for.

### When something goes wrong

The page shows cached data rather than erroring if data could not be fetched for
whatever reason. A message will show describing the error.

You can also use the `/healthz` endpoint, which returns `200`
while the API is answering and `503` once a fetch has failed, e.g
```sh
curl -sf http://127.0.0.1:5000/healthz || echo "forecast data is going stale"
```

## Development notes

The live forecast API currently uses these collections:

- `uk-spot-percentiles` for temperature, wind, humidity, visibility, UV, pressure and weather symbols.
- `uk-spot-probabilities` for the chance of precipitation.

Both collections are queried through an instance, for example:
`/collections/{id}/instances/blended/locations/{siteId}`.

The API key is sent in an `apikey` request header. The response is a
`CoverageCollection` containing a separate time axis for each parameter, so the
application merges values by timestamp.

The service declares its CoverageJSON axes in an order that does not match the order
used in its values. `covjson.choose_order` detects the actual layout from the data:
percentiles increase, while exceedance probabilities decrease.

The location catalogue contains IDs and coordinates but no names. Place searches use
[postcodes.io](https://postcodes.io), and the site name shown on the page comes from
reverse geocoding.

Parameter names are matched by pattern in `parameters.py` because the public glossary is
JavaScript-rendered and the API does not expose its definitions without authentication.
This lets an API rename affect one row rather than breaking the whole page.

Some fields are hourly (`Pt01h`) for the first part of the forecast and three-hourly
(`Pt03h`) later on. The finer resolution is used when both are available. The page
shows the first seven days because weather symbols stop being published after roughly
eight days even though some other fields continue for longer.

## Development commands

Run the test suite:

```sh
uv run pytest
```

Inspect the current API shape and save the responses under `scratch/discovery/`:

```sh
uv run python scripts/discover.py
```

Forecast and instance caches expire when the forecast rolls to a new hour. A five-minute
floor prevents a request made just before the hour from expiring immediately afterwards.
The site catalogue is cached for one week. Cache files record their own write time so a
copy or restored backup does not reset their age.

## Attribution

Forecast and warning data are © Crown copyright, Met Office. The page includes the
“Data supplied by the Met Office” acknowledgement required by the DataHub terms. The
weather symbols were created for this project and do not use the Met Office's artwork.

[terms]: https://www.metoffice.gov.uk/binaries/content/assets/metofficegovuk/pdf/data/met-office-weatherdatahub-terms-and-conditions.pdf
