# NB House Comps

A small local web app: type in an address, and it shows nearby comparable
properties in New Brunswick, Canada, along with their assessed values.

## Important: what this data is (and isn't)

New Brunswick does not publish actual MLS **sold prices** as open data —
that data is only available through paid realtor/MLS board access. This app
instead uses the Government of New Brunswick's open **property assessment**
data (the value your municipal property tax is based on), sourced from the
[GeoNB open data portal](https://gnb.socrata.com/GeoNB/Property-Assessment-Data-Donn-es-sur-l-valuation-f/yqr9-tpe4).
Assessed value is a reasonable public proxy for relative home values, but it
is **not** the price a home actually sold for.

## How it works

1. You enter an address. The app geocodes it (free, via OpenStreetMap
   Nominatim) to get coordinates and the community/town name.
2. It queries the NB open data property assessment dataset, finds properties
   near those coordinates (or, if the dataset doesn't expose per-row
   coordinates, properties in the same community as a fallback), and
   computes distance.
3. It shows the list sorted by distance, along with average/median/min/max
   assessed value among comparable properties, and plots them on a map.

The upstream dataset's exact column names weren't reachable from the
environment this was built in (network policy blocked the GeoNB Socrata
domain during development), so the backend **auto-detects the schema** at
request time (`discover_schema()` in `app.py`) by matching column
name/description keywords instead of hardcoding field names. This makes it
resilient to schema differences, but also means you should actually try it
against the live dataset and see what comes back — see Troubleshooting below
if fields look wrong or empty.

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000 in your browser.

## Usage

- Enter an address like `123 Main St, Fredericton, NB` (include the town —
  it helps geocoding accuracy).
- Adjust the search radius (km) if you want a wider or narrower comp set.
- Results show comparable properties, their assessed values, distance from
  your address, and property class where available.

## Troubleshooting / tuning

- **"Could not find that address"** — try adding the town/city and "NB",
  or double-check spelling. Nominatim (the free geocoder used) is stricter
  about formatting than Google Maps.
- **No assessed value shown for the subject property** — the app does a
  best-effort text search for your address in the dataset; if the exact
  civic address text doesn't match closely enough it may not find a record,
  but nearby comps will still show based on location.
- **"Coordinates not found in the dataset"** — means the auto-detected
  schema didn't find a point/lat/lon column, so it fell back to matching by
  community name only (less precise, no true radius filtering). If you
  inspect the dataset and find the actual coordinate field name, you can
  hardcode it in `discover_schema()` in `app.py` for more precision.
- **Rate limits** — Nominatim asks for no more than ~1 request/second for
  free use; fine for interactive personal use. Socrata's public API also has
  modest default rate limits; if you hit them, get a free app token from
  Socrata/GeoNB and set it as the `SOCRATA_APP_TOKEN` environment variable.

## Tech

- Backend: Flask (`app.py`)
- Geocoding: OpenStreetMap Nominatim (free, no API key)
- Property data: GeoNB / Service New Brunswick open data (Socrata API, no
  API key required for light use)
- Frontend: vanilla JS + Leaflet map (`static/`, `templates/`)
