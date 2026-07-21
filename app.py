"""
NB House Comps — look up nearby comparable properties in New Brunswick.

Data source: Government of New Brunswick open data (GeoNB / Service New
Brunswick), "Property Assessment Data" Socrata dataset. This is ASSESSED
VALUE data (used for property tax), not actual MLS sale prices — real
sold-price data is not published as open data in NB. Assessed value is
used here as a public proxy for "what similar homes nearby are worth".

Because the exact column layout of the upstream Socrata dataset can change
and could not be verified from this environment, the schema is discovered
at runtime from the dataset's metadata endpoint instead of being
hardcoded, using keyword heuristics to find the fields we need (address,
assessed value, property class, coordinates/community). If a field can't
be found, the app degrades gracefully (see `discover_schema` /
`find_nearby`).
"""
import math
import os
import time

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

SOCRATA_DOMAIN = "gnb.socrata.com"
DATASET_ID = "yqr9-tpe4"  # Property Assessment Data / Données sur l'évaluation foncière
SOCRATA_APP_TOKEN = os.environ.get("SOCRATA_APP_TOKEN")  # optional, raises rate limits

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "nb-house-comps/1.0 (personal use; contact via github)"

_schema_cache = {"schema": None, "fetched_at": 0}
SCHEMA_TTL_SECONDS = 3600


def socrata_headers():
    headers = {"Accept": "application/json"}
    if SOCRATA_APP_TOKEN:
        headers["X-App-Token"] = SOCRATA_APP_TOKEN
    return headers


def discover_schema():
    """Fetch the dataset's column metadata and guess which columns hold
    the address, assessed value, property class, and geo coordinates,
    using keyword matching against column names/descriptions."""
    now = time.time()
    if _schema_cache["schema"] and now - _schema_cache["fetched_at"] < SCHEMA_TTL_SECONDS:
        return _schema_cache["schema"]

    resp = requests.get(
        f"https://{SOCRATA_DOMAIN}/api/views/{DATASET_ID}.json",
        headers=socrata_headers(),
        timeout=20,
    )
    resp.raise_for_status()
    meta = resp.json()
    columns = meta.get("columns", [])

    def find_field(keywords, type_filter=None, exclude=()):
        for col in columns:
            name = (col.get("fieldName") or "").lower()
            label = (col.get("name") or "").lower()
            desc = (col.get("description") or "").lower()
            haystack = f"{name} {label} {desc}"
            if any(bad in haystack for bad in exclude):
                continue
            if type_filter and col.get("dataTypeName") not in type_filter:
                continue
            if any(kw in haystack for kw in keywords):
                return col.get("fieldName")
        return None

    point_field = find_field(
        ["location", "geocode", "point", "coordinates"], type_filter=("point",)
    )
    lat_field = find_field(["latitude", "lat"], exclude=("relative",))
    lon_field = find_field(["longitude", "long"])
    address_field = find_field(["address", "civic", "location", "street"])
    value_field = find_field(["assessed_value", "assessed value", "assessment", "value"], exclude=("land",))
    if not value_field:
        value_field = find_field(["value"])
    class_field = find_field(["property_class", "property class", "class", "property_type", "use"])
    community_field = find_field(["community", "parish", "municipality", "town", "city"])
    pid_field = find_field(["pid", "parcel"])

    schema = {
        "point_field": point_field,
        "lat_field": lat_field,
        "lon_field": lon_field,
        "address_field": address_field,
        "value_field": value_field,
        "class_field": class_field,
        "community_field": community_field,
        "pid_field": pid_field,
        "all_field_names": [c.get("fieldName") for c in columns],
    }
    _schema_cache["schema"] = schema
    _schema_cache["fetched_at"] = now
    return schema


def geocode_address(address):
    query = address if "new brunswick" in address.lower() or ", nb" in address.lower() else f"{address}, New Brunswick, Canada"
    params = {
        "format": "json",
        "q": query,
        "countrycodes": "ca",
        "addressdetails": 1,
        "limit": 1,
    }
    resp = requests.get(
        NOMINATIM_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=15
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None
    top = results[0]
    addr = top.get("address", {})
    return {
        "lat": float(top["lat"]),
        "lon": float(top["lon"]),
        "display_name": top.get("display_name"),
        "community": addr.get("city") or addr.get("town") or addr.get("village") or addr.get("hamlet"),
        "postcode": addr.get("postcode"),
    }


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def row_lat_lon(row, schema):
    if schema["point_field"] and schema["point_field"] in row:
        pt = row[schema["point_field"]]
        coords = pt.get("coordinates") if isinstance(pt, dict) else None
        if coords and len(coords) == 2:
            return coords[1], coords[0]  # GeoJSON is [lon, lat]
    if schema["lat_field"] and schema["lon_field"]:
        try:
            return float(row[schema["lat_field"]]), float(row[schema["lon_field"]])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def parse_value(row, schema):
    if not schema["value_field"]:
        return None
    raw = row.get(schema["value_field"])
    if raw is None:
        return None
    try:
        return float(str(raw).replace("$", "").replace(",", ""))
    except ValueError:
        return None


def fetch_rows(params):
    resp = requests.get(
        f"https://{SOCRATA_DOMAIN}/resource/{DATASET_ID}.json",
        params=params,
        headers=socrata_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def find_subject_record(address, schema):
    """Best-effort full-text search for the subject property's own record,
    so we know its assessed value / property class as a baseline."""
    if not schema["address_field"]:
        return None
    params = {"$q": address, "$limit": 5}
    try:
        rows = fetch_rows(params)
    except requests.RequestException:
        return None
    return rows[0] if rows else None


def find_nearby(geo, radius_km, limit=200):
    schema = discover_schema()
    community = geo.get("community")

    params = {"$limit": limit}
    if community:
        params["$q"] = community

    try:
        rows = fetch_rows(params)
    except requests.RequestException as exc:
        return {"error": f"Could not reach NB open data service: {exc}"}, schema

    have_coords = schema["point_field"] or (schema["lat_field"] and schema["lon_field"])
    results = []
    for row in rows:
        latlon = row_lat_lon(row, schema) if have_coords else None
        dist_km = None
        if latlon:
            dist_km = haversine_km(geo["lat"], geo["lon"], latlon[0], latlon[1])
            if dist_km > radius_km:
                continue
        value = parse_value(row, schema)
        results.append(
            {
                "address": row.get(schema["address_field"]) if schema["address_field"] else None,
                "assessed_value": value,
                "property_class": row.get(schema["class_field"]) if schema["class_field"] else None,
                "pid": row.get(schema["pid_field"]) if schema["pid_field"] else None,
                "distance_km": round(dist_km, 3) if dist_km is not None else None,
                "lat": latlon[0] if latlon else None,
                "lon": latlon[1] if latlon else None,
            }
        )

    if have_coords:
        results.sort(key=lambda r: (r["distance_km"] is None, r["distance_km"]))
    else:
        results.sort(key=lambda r: (r["assessed_value"] is None, r["assessed_value"] or 0))

    return {
        "precise_geo": bool(have_coords),
        "results": results,
    }, schema


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search")
def api_search():
    address = request.args.get("address", "").strip()
    radius_km = float(request.args.get("radius_km", 1.0))
    if not address:
        return jsonify({"error": "Please enter an address."}), 400

    try:
        geo = geocode_address(address)
    except requests.RequestException as exc:
        return jsonify({"error": f"Geocoding failed: {exc}"}), 502

    if not geo:
        return jsonify({"error": "Could not find that address. Try including the town and 'NB', e.g. '123 Main St, Fredericton, NB'."}), 404

    schema = discover_schema()
    subject = find_subject_record(address, schema)
    subject_value = parse_value(subject, schema) if subject else None
    subject_class = subject.get(schema["class_field"]) if subject and schema["class_field"] else None

    nearby, _ = find_nearby(geo, radius_km)
    if "error" in nearby:
        return jsonify({"error": nearby["error"]}), 502

    comparables = nearby["results"]

    equivalent = comparables
    if subject_class:
        same_class = [c for c in comparables if c["property_class"] == subject_class]
        if same_class:
            equivalent = same_class

    values = [c["assessed_value"] for c in equivalent if c["assessed_value"] is not None]
    stats = None
    if values:
        values_sorted = sorted(values)
        n = len(values_sorted)
        median = values_sorted[n // 2] if n % 2 else (values_sorted[n // 2 - 1] + values_sorted[n // 2]) / 2
        stats = {
            "count": n,
            "average": round(sum(values) / n, 2),
            "median": round(median, 2),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
        }

    return jsonify(
        {
            "subject": {
                "input_address": address,
                "matched_address": geo["display_name"],
                "lat": geo["lat"],
                "lon": geo["lon"],
                "community": geo.get("community"),
                "assessed_value": subject_value,
                "property_class": subject_class,
            },
            "precise_geo": nearby["precise_geo"],
            "radius_km": radius_km,
            "stats": stats,
            "comparables": comparables[:100],
        }
    )


if __name__ == "__main__":
    # host="0.0.0.0" makes this reachable from other devices on the same
    # network (e.g. your phone), not just this machine.
    app.run(debug=True, port=5000, host="0.0.0.0")
