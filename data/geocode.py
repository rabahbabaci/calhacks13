"""US address normalization through the Census Geocoder (free, no key, every US address).

geocode("381 8th St, Oakland, CA 94607") -> {
    "input", "matched_address", "house_number", "street_name", "street_suffix",
    "city", "place", "postal_city", "county", "county_fips", "state", "state_fips", "zip", "lat", "lon"
}
Returns None when the Census has no match. house_number comes from the input, because
the Census match only gives the address range of the street segment.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
TIMEOUT = 30


def _first(items, key):
    return (items or [{}])[0].get(key) or ""


def parse_house_number(address):
    m = re.match(r"\s*(\d+[A-Za-z]?(?:-\d+)?)\s", address or "")
    return m.group(1) if m else ""


PLACE_TYPES = {"city", "town", "village", "borough", "cdp"}


def strip_place_type(place):
    """'Oakland city' -> 'Oakland'; Census place names end with their type."""
    words = (place or "").split()
    if len(words) > 1 and words[-1].lower() in PLACE_TYPES:
        words = words[:-1]
    return " ".join(words)


def geocode(address):
    params = {
        "address": address,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "layers": "Counties,Incorporated Places,States",
        "format": "json",
    }
    try:
        with urllib.request.urlopen(f"{URL}?{urllib.parse.urlencode(params)}", timeout=TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    matches = body.get("result", {}).get("addressMatches") or []
    if not matches:
        return None
    m = matches[0]
    parts = m.get("addressComponents", {})
    geo = m.get("geographies", {})
    place = _first(geo.get("Incorporated Places"), "NAME")
    return {
        "input": address,
        "matched_address": m.get("matchedAddress", ""),
        "house_number": parse_house_number(address),
        "street_name": " ".join(x for x in [parts.get("preDirection"), parts.get("preType"), parts.get("streetName")] if x),
        "street_suffix": parts.get("suffixType", ""),
        "city": strip_place_type(place) or parts.get("city", "").title(),
        "place": place,
        # The postal city can differ from the incorporated place (Brooklyn vs New York).
        "postal_city": parts.get("city", "").title(),
        "county": _first(geo.get("Counties"), "NAME"),
        "county_fips": _first(geo.get("Counties"), "GEOID"),
        "state": parts.get("state", "") or _first(geo.get("States"), "STUSAB"),
        "state_fips": _first(geo.get("States"), "GEOID"),
        "zip": parts.get("zip", ""),
        "lat": m.get("coordinates", {}).get("y"),
        "lon": m.get("coordinates", {}).get("x"),
    }


if __name__ == "__main__":
    import sys

    print(json.dumps(geocode(" ".join(sys.argv[1:]) or "381 8th St, Oakland, CA 94607"), indent=2))
