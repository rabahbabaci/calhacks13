"""Nearby testing sites. A directory response is parsed; nothing is invented.

With no directory payload, the only result is the HRSA search page for the ZIP.
A cited directory keeps a center when that https page states the name.
Tests, insurance, and funding still come only from that page. The street
address may be copied from a later page when the cited page does not state one.
"""
import json
import math
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

HRSA = "https://findahealthcenter.hrsa.gov/"
ROOT = Path(__file__).resolve().parents[1]
ALLOWED_HOSTS = {
    "findahealthcenter.hrsa.gov",
    "acphd.org",
    "oaklandca.gov",
    # acphd.org currently answers 403. The department pages load here.
    "health.alamedacountyca.gov",
}
_SITE_WORD = re.compile(
    r"\b(clinic|health center|health centre|office|hospital|center|centre|program|site|laboratory|lab)\b",
    re.I,
)
FUNDING_VALUES = {"", "free", "city-funded", "free, city-funded"}
PLAN_NAMES = (
    "Medi-Cal",
    "Medicare",
    "Covered California",
    "Family PACT",
    "HealthPAC",
    "Blue Shield",
    "Blue Cross",
    "Kaiser Permanente",
    "Kaiser",
    "Anthem",
    "Aetna",
    "Cigna",
    "UnitedHealthcare",
    "United Healthcare",
    "Health Net",
    "Alameda Alliance",
)
_STREET = (
    r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Way|Lane|Ln|"
    r"Court|Ct|Place|Pl|Parkway|Pkwy)"
)
_ADDRESS = re.compile(
    rf"(\d{{1,6}}\s+[A-Za-z0-9][A-Za-z0-9.'’\-]*"
    rf"(?:\s+[A-Za-z0-9][A-Za-z0-9.'’\-]*){{0,5}}\s+{_STREET}\b\.?"
    rf"(?:,\s*[A-Za-z .'-]+(?:,\s*[A-Z]{{2}})?(?:\s+\d{{5}})?)?)",
    re.I,
)
_SKIP_NAME = {
    "home",
    "search",
    "menu",
    "contact",
    "about",
    "skip to content",
    "find a health center",
}


HRSA_LOCATOR = "https://data.hrsa.gov/HDWLocatorApi/healthcenters/find"
# The old HDW locator now serves an HTML error page. The public GIS layer is
# the same Health Center Service Delivery Sites list.
HRSA_GIS = (
    "https://gisportal.hrsa.gov/server/rest/services/HealthCareFacilities/"
    "HealthCareFacilities/MapServer/18/query"
)
# Same locator the Find a Health Center page calls after it places the ZIP.
# This is the ArcGIS geocoder that page uses, not the Census geocoder.
HRSA_GEOCODER = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
HRSA_RADIUS_MI = 5


def _get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://findahealthcenter.hrsa.gov/",
        "Origin": "https://findahealthcenter.hrsa.gov",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode()
        if not body.lstrip().startswith(("{", "[")):
            raise ValueError("not json")
        return json.loads(body)


def _hrsa_address(row):
    street = str(row.get("CtrAddress") or "").strip()
    city = str(row.get("CtrCity") or "").strip()
    state = str(row.get("CtrStateAbbr") or "").strip()
    zip_code = str(row.get("CtrZipCd") or "").split("-")[0].strip()
    tail = ", ".join(part for part in (city, state) if part)
    if zip_code:
        tail = f"{tail} {zip_code}".strip()
    return ", ".join(part for part in (street, tail) if part)


def clinics_from_hrsa(rows, zip_code):
    """One tile per ZIP-search result. A missing center name does not drop the row."""
    url = search_url(zip_code)
    clinics = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        address = _hrsa_address(row)
        name = str(row.get("CtrNm") or "").strip() or address
        if not name and not address:
            continue
        lat = row.get("Latitude")
        lon = row.get("Longitude")
        distance = row.get("Distance")
        try:
            distance = float(distance) if distance is not None and distance != "" else None
        except (TypeError, ValueError):
            distance = None
        clinic = _record(
            name, address, _maps(lat, lon, address), distance,
            str(row.get("CtrPhoneNum") or "").strip(),
            "", "", "", url, lat, lon,
        )
        clinic["website"] = _website(row.get("SiteUrl") or row.get("UrlTxt"))
        clinic["parent"] = str(row.get("ParentCtrNm") or "").strip()
        clinics.append(clinic)
    clinics.sort(key=lambda item: (item["distance_mi"] is None, item["distance_mi"] or 0))
    return clinics


def _hrsa_rows_from_gis(features, origin=None):
    """Map the public HRSA GIS layer to the same row shape clinics_from_hrsa reads."""
    rows = []
    for feat in features or []:
        if not isinstance(feat, dict):
            continue
        attrs = feat.get("attributes") or {}
        geom = feat.get("geometry") or {}
        lon = geom.get("x") if geom.get("x") is not None else attrs.get("X")
        lat = geom.get("y") if geom.get("y") is not None else attrs.get("Y")
        distance = None
        if origin and lon is not None and lat is not None:
            try:
                distance = round(_miles(origin, (float(lon), float(lat))), 2)
            except (TypeError, ValueError):
                distance = None
        rows.append({
            "CtrNm": attrs.get("SITE_NM") or "",
            "CtrAddress": attrs.get("SITE_ADDRESS") or "",
            "CtrCity": attrs.get("SITE_CITY") or "",
            "CtrStateAbbr": attrs.get("SITE_STATE_ABBR") or "",
            "CtrZipCd": attrs.get("SITE_ZIP_CD") or "",
            "CtrPhoneNum": attrs.get("SITE_PHONE_NUM") or "",
            "Latitude": lat,
            "Longitude": lon,
            "Distance": distance,
            "SiteUrl": attrs.get("SITE_URL") or "",
            "ParentCtrNm": attrs.get("HCC_OPER_DESC") or "",
        })
    return rows


_HRSA_CACHE = {}
_ADDR_GEO = {}


def hrsa_cached(zip_code):
    zip_code = re.sub(r"\D", "", zip_code or "")[:5]
    return _HRSA_CACHE.get(zip_code)


def hrsa_directory(zip_code, timeout=30):
    """The same 5-mile list the HRSA ZIP search shows. No Apify and no Census geocoder."""
    zip_code = re.sub(r"\D", "", zip_code or "")[:5]
    if len(zip_code) != 5:
        return {"clinics": [], "note": "No cited page listed a center name. Home does not invent clinic names."}
    if zip_code in _HRSA_CACHE:
        return _HRSA_CACHE[zip_code]
    try:
        geo = _get_json(HRSA_GEOCODER + "?" + urllib.parse.urlencode({
            "f": "json", "singleLine": zip_code, "countryCode": "USA", "maxLocations": "1",
        }), timeout=timeout)
        candidates = geo.get("candidates") or []
        if not candidates:
            raise ValueError("no location")
        location = candidates[0]["location"]
        origin = (float(location["x"]), float(location["y"]))
        data = _get_json(HRSA_GIS + "?" + urllib.parse.urlencode({
            "f": "json",
            "geometry": f"{origin[0]},{origin[1]}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "distance": str(HRSA_RADIUS_MI),
            "units": "esriSRUnit_StatuteMile",
            "outFields": "SITE_NM,SITE_ADDRESS,SITE_CITY,SITE_STATE_ABBR,SITE_ZIP_CD,SITE_PHONE_NUM,SITE_URL,HCC_OPER_DESC,X,Y",
            "returnGeometry": "true",
            "outSR": "4326",
            "resultRecordCount": "50",
        }), timeout=timeout)
        if data.get("error"):
            raise ValueError(data["error"])
        rows = _hrsa_rows_from_gis(data.get("features") or [], origin)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return {"clinics": [], "note": "The HRSA ZIP search did not return a center list. Home does not invent clinic names."}
    clinics = clinics_from_hrsa(rows, zip_code)
    note = "" if clinics else "No cited page listed a center name. Home does not invent clinic names."
    if clinics:
        note = "These are the sites the HRSA ZIP search lists within 5 miles. That result does not list tests or insurance."
    out = {"clinics": clinics, "note": note}
    if clinics:
        _HRSA_CACHE[zip_code] = out
    return out


def search_url(zip_code):
    if zip_code:
        return HRSA + "?" + urllib.parse.urlencode({"zip": zip_code})
    return HRSA


def _maps(lat, lon, address):
    if lat is not None and lon is not None:
        return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(f"{lat},{lon}")
    if address:
        return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(address)
    return ""


def _norm(text):
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _contains(page, snippet):
    snippet = _norm(snippet)
    if not snippet:
        return False
    return snippet in _norm(page)


def _coords(lat, lon):
    try:
        if lat is None or lon is None or lat == "" or lon == "":
            return None, None
        lat, lon = float(lat), float(lon)
        if not (math.isfinite(lat) and math.isfinite(lon)):
            return None, None
        if abs(lat) > 90 or abs(lon) > 180:
            return None, None
        return lat, lon
    except (TypeError, ValueError):
        return None, None


def _record(name, address, maps, distance, phone, tests, insurance, funding, source_url, lat=None, lon=None):
    row = {
        "name": name,
        "address": address,
        "distance_mi": distance,
        "phone": phone,
        "maps": maps,
        "tests": tests,
        "insurance": insurance,
        "funding": funding,
        "source_url": source_url,
    }
    lat, lon = _coords(lat, lon)
    if lat is not None and lon is not None:
        row["lat"] = lat
        row["lon"] = lon
    return row


def _stated(value, text):
    """Keep only the parts of a claimed tests or insurance string that the page contains."""
    parts = [part.strip() for part in re.split(r",|;", str(value or "")) if part.strip()]
    kept = [part for part in parts if _contains(text, part)]
    return ", ".join(kept)


def _funding(text):
    low = (text or "").lower()
    free = bool(re.search(r"(?<!feel\s)\bfree\b|no-cost|no cost", low))
    city = "city-funded" in low or "city funded" in low
    parts = []
    if free:
        parts.append("free")
    if city:
        parts.append("city-funded")
    return ", ".join(parts)


def _distance(text):
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:miles|mile|mi)\b", text or "", re.I)
    if not match:
        return None
    return float(match.group(1))


def _page_text(item, pages):
    text = str(item.get("page_text") or "").strip()
    if text:
        return text
    return str(pages.get(str(item.get("source_url") or "").strip()) or "")


def parse_directory(payload):
    """Accept {clinics: [...]} or a Places-style {results: [...]}.

    When a row carries page text, or the payload has a pages map, a row is kept
    only if its https source_url is set and both the name and address appear in
    that page. Tests, insurance, and funding are copied from that same page.
    """
    rows = []
    pages = {}
    if isinstance(payload, dict):
        rows = payload.get("clinics") or payload.get("results") or []
        pages = payload.get("pages") or {}
    citing = bool(pages) or any(
        isinstance(item, dict) and str(item.get("page_text") or "").strip() for item in rows
    )
    out = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or (item.get("displayName") or {}).get("text") or ""
        name = str(name).strip()
        if not name:
            continue
        loc = item.get("location") or {}
        lat = item.get("lat", loc.get("latitude"))
        lon = item.get("lon", loc.get("longitude"))
        address = str(item.get("address") or item.get("formattedAddress") or "").strip()
        maps = _maps(lat, lon, address)
        phone = str(item.get("phone") or "").strip()
        source_url = str(item.get("source_url") or "").strip()
        if not citing:
            funding = str(item.get("funding") or "").strip()
            if funding not in FUNDING_VALUES:
                funding = ""
            record = _record(
                name, address, maps, item.get("distance_mi"), phone,
                str(item.get("tests") or "").strip(),
                str(item.get("insurance") or "").strip(),
                funding, source_url, lat, lon,
            )
            record["parent"] = str(item.get("parent") or "").strip()
            out.append(record)
            continue
        text = _page_text(item, pages)
        if not source_url.startswith("https://"):
            continue
        if not _contains(text, name):
            continue
        looked_up = str(item.get("address_page_text") or "")
        if address and not _contains(text, address) and not _contains(looked_up, address):
            address = ""
            maps = _maps(lat, lon, "")
        elif address and not _contains(text, address):
            maps = _maps(lat, lon, address)
        if phone and not _contains(text, phone):
            phone = ""
        record = _record(
            name, address, maps, _distance(text), phone,
            _stated(item.get("tests"), text),
            _stated(item.get("insurance"), text),
            _funding(text), source_url, lat, lon,
        )
        record["parent"] = str(item.get("parent") or "").strip()
        out.append(record)
    return out


NPI_API = "https://npiregistry.cms.hhs.gov/api/"
NPI_SPECIALTIES = (
    ("Allergy & Immunology", "allergy"),
    ("Otolaryngology", "ent"),
)
_SPECIALTY_DEFECTS = {"dampness_mold", "water_leak", "cockroaches", "rodents"}
NPI_NEAR_MI = 5
_NPI_CACHE = {}
_ZIP_GEO = {}


def npi_cached(zip_code, city, state="CA"):
    """Return a prior NPI lookup for this ZIP and city, or None if none is ready."""
    zip_code = _zip5(zip_code)
    city = re.sub(r"\s+", " ", city or "").strip()
    if len(zip_code) != 5:
        return None
    return _NPI_CACHE.get((zip_code, city.upper(), (state or "CA").upper()))


def _zip5(value):
    return re.sub(r"\D", "", str(value or ""))[:5]


def specialty_needed(defects):
    return any(tag in _SPECIALTY_DEFECTS for tag in defects or [])


def _npi_fetch(params):
    url = NPI_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    if not isinstance(data, dict):
        return []
    return data.get("results") or []


def _npi_pages(fetch, params):
    found = []
    for skip in (0, 200):
        page = fetch({**params, "limit": "200", "skip": str(skip)}) or []
        found.extend(page)
        if len(page) < 200:
            break
    return found


def _practice_location(record):
    for addr in record.get("addresses") or []:
        if str(addr.get("address_purpose") or "").upper() == "LOCATION":
            return addr
    return None


_STREET_SUFFIX = {
    "STREET": "ST", "AVENUE": "AVE", "BOULEVARD": "BLVD", "ROAD": "RD",
    "DRIVE": "DR", "LANE": "LN", "COURT": "CT", "PLACE": "PL",
}
_STREET_DIR = {"EAST": "E", "WEST": "W", "NORTH": "N", "SOUTH": "S"}


def _street_key(addr):
    line = str(addr.get("address_1") or "").upper().replace(".", " ")
    line = re.sub(r"\b(?:STE|SUITE|UNIT|APT|FL|FLOOR|#|BLDG|BUILDING)\b.*", "", line)
    parts = [_STREET_DIR.get(part, _STREET_SUFFIX.get(part, part)) for part in re.findall(r"[A-Z0-9]+", line)]
    if parts and parts[-1] in _STREET_SUFFIX.values():
        parts = parts[:-1]
    return "".join(parts)


def _format_practice(addr):
    street = re.sub(r"\s+", " ", str(addr.get("address_1") or "")).strip()
    city = str(addr.get("city") or "").strip()
    state = str(addr.get("state") or "").strip()
    zip_code = _zip5(addr.get("postal_code"))
    tail = " ".join(part for part in (state, zip_code) if part)
    if city:
        tail = f"{city}, {tail}".strip()
    return ", ".join(part for part in (street, tail) if part)


def _practice_name(records, kinds):
    for record in records:
        org = str((record.get("basic") or {}).get("organization_name") or "").strip()
        if org and record.get("enumeration_type") == "NPI-2":
            return org
    for record in records:
        org = str((record.get("basic") or {}).get("organization_name") or "").strip()
        if org:
            return org
    if "allergy" in kinds and "ent" in kinds:
        return "Allergy and ear, nose, and throat office"
    if "ent" in kinds:
        return "Ear, nose, and throat office"
    return "Allergy and immunology office"


def _chosen_npi(records):
    for record in records:
        if record.get("enumeration_type") == "NPI-2" and record.get("number"):
            return str(record["number"])
    for record in records:
        if record.get("number"):
            return str(record["number"])
    return ""


def _geocode_address(address):
    """Street or ZIP to (lat, lon). Same ArcGIS locator as the HRSA search."""
    key = re.sub(r"\s+", " ", address or "").strip()
    if not key:
        return None, None
    if key in _ADDR_GEO:
        return _ADDR_GEO[key]
    try:
        geo = _get_json(HRSA_GEOCODER + "?" + urllib.parse.urlencode({
            "f": "json", "singleLine": key, "countryCode": "USA", "maxLocations": "1",
        }), timeout=8)
        location = ((geo.get("candidates") or [{}])[0].get("location") or {})
        point = _coords(location.get("y"), location.get("x"))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        point = (None, None)
    _ADDR_GEO[key] = point
    return point


def attach_coords(clinics):
    """Fill lat/lon so the nearby-testing map can place a site without a browser geocode."""
    for row in clinics or []:
        if not isinstance(row, dict):
            continue
        if row.get("lat") is not None and row.get("lon") is not None:
            continue
        lat, lon = _geocode_address(row.get("address") or "")
        if lat is None:
            continue
        row["lat"] = lat
        row["lon"] = lon
        row["maps"] = _maps(lat, lon, row.get("address") or "")
    return clinics


def _geocode_zip(zip_code):
    """Place a ZIP with the same ArcGIS locator the HRSA search uses. Not the Census geocoder."""
    zip_code = _zip5(zip_code)
    if zip_code in _ZIP_GEO:
        return _ZIP_GEO[zip_code]
    point = None
    try:
        geo = _get_json(HRSA_GEOCODER + "?" + urllib.parse.urlencode({
            "f": "json", "singleLine": zip_code, "countryCode": "USA", "maxLocations": "1",
        }))
        location = ((geo.get("candidates") or [{}])[0].get("location") or {})
        if location.get("x") is not None and location.get("y") is not None:
            point = (float(location["x"]), float(location["y"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        point = None
    _ZIP_GEO[zip_code] = point
    return point


def _miles(a, b):
    lon1, lat1 = a
    lon2, lat2 = b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlamb = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlamb / 2) ** 2
    return 2 * 3958.8 * math.asin(min(1.0, math.sqrt(h)))


def _near_zip(home, other):
    """True when the practice ZIP is the entered ZIP or within 5 miles of it."""
    home, other = _zip5(home), _zip5(other)
    if not home or not other:
        return False
    if home == other:
        return True
    a, b = _geocode_zip(home), _geocode_zip(other)
    if not a or not b:
        return False
    return _miles(a, b) <= NPI_NEAR_MI


def npi_specialists(zip_code, city, state="CA", fetch=None, near=None, plans=None):
    """Allergy and ear, nose, and throat offices at this ZIP or a neighboring ZIP.

    The registry's postal-code query returns offices across the state, so the
    practice-location ZIP is checked after the response. A neighboring ZIP is
    one whose centroid is within 5 miles of the entered ZIP. City and state
    come from the entered address. Any California ZIP works the same way.
    """
    zip_code = _zip5(zip_code)
    state = (state or "CA").upper()
    city = re.sub(r"\s+", " ", city or "").strip()
    if len(zip_code) != 5:
        return []
    fetch = fetch or _npi_fetch
    if near is None:
        near = (lambda home, other: home == other) if fetch is not _npi_fetch else _near_zip
    cache_key = None
    if fetch is _npi_fetch and near is _near_zip:
        cache_key = (zip_code, city.upper(), state)
        if cache_key in _NPI_CACHE:
            return _NPI_CACHE[cache_key]
    grouped = {}
    for taxonomy, kind in NPI_SPECIALTIES:
        queries = []
        if city:
            queries.append({"city": city, "state": state})
        queries.append({"postal_code": zip_code, "state": state})
        seen_numbers = set()
        for extra in queries:
            try:
                pages = _npi_pages(fetch, {
                    "version": "2.1",
                    "taxonomy_description": taxonomy,
                    "address_purpose": "LOCATION",
                    **extra,
                })
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            for record in pages:
                number = str(record.get("number") or "")
                if number and number in seen_numbers:
                    continue
                addr = _practice_location(record)
                practice_zip = _zip5((addr or {}).get("postal_code"))
                if not addr or not practice_zip or not near(zip_code, practice_zip):
                    continue
                if str(addr.get("state") or "").upper() not in ("", state):
                    continue
                if number:
                    seen_numbers.add(number)
                key = (_street_key(addr), practice_zip, state)
                if not key[0]:
                    continue
                bucket = grouped.setdefault(key, {"addr": addr, "records": [], "kinds": []})
                if len(str(addr.get("address_1") or "")) < len(str(bucket["addr"].get("address_1") or "")):
                    bucket["addr"] = addr
                bucket["records"].append(record)
                if kind not in bucket["kinds"]:
                    bucket["kinds"].append(kind)
    clinics = []
    for bucket in grouped.values():
        addr = bucket["addr"]
        kinds = bucket["kinds"]
        number = _chosen_npi(bucket["records"])
        address = _format_practice(addr)
        phone = str(addr.get("telephone_number") or "").strip()
        clinics.append({
            "name": _practice_name(bucket["records"], kinds),
            "address": address,
            "distance_mi": None,
            "phone": phone,
            "maps": _maps(None, None, address),
            "tests": "",
            "assumed": ", ".join(kinds),
            "insurance": "",
            "assumed_insurance": "",
            "funding": "",
            "source_url": "https://npiregistry.cms.hhs.gov/provider-view/" + number if number else "https://npiregistry.cms.hhs.gov/",
            "parent": "",
        })
    clinics.sort(key=lambda row: row["name"])
    if plans is not None:
        fill_npi_plans(clinics, check=plans)
    elif fetch is _npi_fetch:
        fill_npi_plans(clinics)
        attach_coords(clinics)
    if cache_key is not None:
        _NPI_CACHE[cache_key] = clinics
    return clinics


def _is_npi(row):
    return "npiregistry.cms.hhs.gov" in str(row.get("source_url") or "")


def _is_hrsa(row):
    return "findahealthcenter.hrsa.gov" in str(row.get("source_url") or "")


def _npi_from_row(row):
    match = re.search(r"/provider-view/(\d{10})", str(row.get("source_url") or ""))
    return match.group(1) if match else ""


CMS_ENROLL = "https://data.cms.gov/data-api/v1/dataset/2457ea29-fc82-48b0-86ec-3b0755de7515/data"
CHHS_MEDI_CAL = "https://data.chhs.ca.gov/api/3/action/datastore_search"
CHHS_RESOURCE = "d652b210-ec3d-4a92-b7e0-e55c3dcbc7dc"
_NPI_PLAN_CACHE = {}


def _cms_medicare(npi):
    url = f"{CMS_ENROLL}?filter[NPI]={urllib.parse.quote(npi)}&size=1"
    data = _get_json(url)
    return bool(isinstance(data, list) and data)


def _chhs_medi_cal(npi):
    url = CHHS_MEDI_CAL + "?" + urllib.parse.urlencode({
        "resource_id": CHHS_RESOURCE,
        "filters": json.dumps({"NPI": npi}),
        "limit": "1",
    })
    data = _get_json(url)
    return bool(((data.get("result") or {}).get("records")))


def directory_plans(npi, check=None):
    """Medi-Cal and Medicare from the DHCS and CMS enrollment files. No commercial plans."""
    npi = re.sub(r"\D", "", str(npi or ""))
    if len(npi) != 10:
        return []
    if check is not None:
        return list(check(npi) or [])
    if npi in _NPI_PLAN_CACHE:
        return list(_NPI_PLAN_CACHE[npi])
    plans = []
    try:
        if _chhs_medi_cal(npi):
            plans.append("Medi-Cal")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        pass
    try:
        if _cms_medicare(npi):
            plans.append("Medicare")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        pass
    _NPI_PLAN_CACHE[npi] = plans
    return list(plans)


def fill_npi_plans(clinics, check=None):
    for row in clinics:
        if not _is_npi(row):
            continue
        npi = _npi_from_row(row)
        if not npi:
            continue
        row["assumed_insurance"] = ", ".join(directory_plans(npi, check))
    return clinics


def _health_center_plans(row):
    """Medi-Cal and Medicare that a medical health center or hospital usually accepts."""
    if not _is_hrsa(row):
        return []
    name = row.get("name") or ""
    parent = row.get("parent") or ""
    if not (_is_primary_care(name, parent) or _is_hospital_or_emergency(name)):
        return []
    stated = row.get("insurance") or ""
    plans = []
    if not _matches_needed(stated, ["Medi-Cal"]):
        plans.append("Medi-Cal")
    if not _matches_needed(stated, ["Medicare"]):
        plans.append("Medicare")
    return plans


def lookup(zip_code, see_a_doctor_if, directory=None, defects=None, specialists=None):
    url = search_url(zip_code or "")
    reason = ""
    for line in see_a_doctor_if or []:
        text = line.get("text") if isinstance(line, dict) else str(line)
        if text:
            reason = text
            break
    if not directory:
        clinics = []
        note = "No clinic directory key is set. This opens the HRSA health-center search for your ZIP. Home does not invent clinic names."
        source = "hrsa_link"
    else:
        note = ""
        if isinstance(directory, dict) and "note" in directory:
            note = str(directory.get("note") or "")
        clinics = parse_directory(directory)
        source = "directory"
        if defects is not None:
            needed = tests_for_defects(defects)
            kept = []
            for row in clinics:
                stated = _matching_stated(row.get("tests"), needed)
                assumed = _assumed_tests(row.get("name"), row.get("parent") or "", needed, stated)
                if not stated and not assumed:
                    continue
                row["tests"] = ", ".join(stated)
                row["assumed"] = ", ".join(assumed)
                row["assumed_insurance"] = ", ".join(_health_center_plans(row))
                kept.append(row)
            clinics = kept
            if clinics:
                note = _filter_note(clinics)
            elif needed:
                note = "No center matches a test these defects need."
            else:
                note = "These defects do not name a test a health center would list."
    if defects is not None and specialty_needed(defects) and specialists:
        clinics = list(clinics) + list(specialists)
        if clinics:
            note = _filter_note(clinics)
    return {
        "source": source,
        "note": note,
        "reason": reason,
        "search_url": url,
        "clinics": clinics,
    }


def _clean_line(raw):
    text = re.sub(r"^#+\s*", "", raw or "")
    text = re.sub(r"\[(.*?)\]\([^)]*\)", r"\1", text)
    text = text.replace("**", "").replace("__", "")
    return re.sub(r"\s+", " ", text).strip(" *-:>|")


def _name_above(lines, index):
    for cursor in range(index - 1, max(-1, index - 4), -1):
        raw = _clean_line(lines[cursor])
        if not raw or _ADDRESS.search(raw):
            continue
        if len(raw) < 3 or len(raw) > 80:
            continue
        if raw.endswith(".") and len(raw) > 40:
            continue
        if raw.lower() in _SKIP_NAME:
            continue
        if not re.search(r"[A-Za-z]", raw):
            continue
        return raw
    return ""


def _pairs(text):
    found = []
    seen = set()
    lines = text.splitlines() or [text]
    for index, line in enumerate(lines):
        match = _ADDRESS.search(_clean_line(line))
        if not match:
            continue
        address = re.sub(r"\s+", " ", match.group(1)).strip(" .,;")
        name = _name_above(lines, index)
        if not name or _norm(name) in _norm(address):
            continue
        key = (_norm(name), _norm(address))
        if key in seen:
            continue
        seen.add(key)
        found.append((name, address))
    return found


def _window(text, address):
    low = text.lower()
    idx = low.find(address.lower())
    if idx < 0:
        return text
    start = max(0, idx - 500)
    end = min(len(text), idx + len(address) + 700)
    return text[start:end]


# Longer names first so "blood lead" is kept and a later "lead" is not added again.
_NAMED_TESTS = (
    "hepatitis B", "hepatitis C", "COVID-19", "tuberculosis", "chlamydia",
    "gonorrhea", "syphilis", "hepatitis", "blood lead", "allergy", "mold",
    "lead", "HIV", "STI", "STD", "mpox",
)
# Tests a health-center page would have to state for that housing defect.
# Words already used for lead in the Oakland pack and the fixture. Mold,
# dampness, leaks, cockroaches, and rodents match allergy or mold testing
# when the center's own page says so. Other defects name no clinic test.
DEFECT_TESTS = {
    "lead_paint": ("lead",),
    "dampness_mold": ("mold", "allergy"),
    "water_leak": ("mold", "allergy"),
    "cockroaches": ("allergy",),
    "rodents": ("allergy",),
    "carbon_monoxide": ("carboxyhemoglobin",),
}
# The site's own name decides the type. A parent health center cannot add a test.
_NOT_PRIMARY = re.compile(
    r"\b(?:dental|wic|optical|optometr\w*|vision|pharmacy|"
    r"behavioral health|behavioural health|mental health|"
    r"street[\s-]?health|shelter[\s-]?health|mobile)\b",
    re.I,
)
_PRIMARY = re.compile(
    r"\b(?:clinic|health cent(?:er|re)|family practice|family medicine|"
    r"pediatric|pediatrics|primary care|medical cent(?:er|re)|medical care)\b",
    re.I,
)
_HOSPITAL_VISIT = re.compile(r"\b(?:clinic|primary care|pediatric|pediatrics|school)\b", re.I)
_HOSPITAL = re.compile(r"\bhospital\b", re.I)
_EMERGENCY = re.compile(r"\b(?:emergency department|emergency room)\b", re.I)
_ALLERGY_SITE = re.compile(r"\ballergy\b", re.I)


def tests_for_defects(defects):
    """Test names the case defects can match. Order follows the defects."""
    found = []
    for tag in defects or []:
        for name in DEFECT_TESTS.get(tag, ()):
            if name not in found:
                found.append(name)
    return found


# Assumed kinds use longer labels than DEFECT_TESTS. Map them back so a
# blood-lead or ENT tile lands under the hazard that needed that test.
_ASSUMED_FOR = {
    "blood lead": ("lead",),
    "allergy": ("mold", "allergy"),
    "ent": ("mold", "allergy"),
    "carboxyhemoglobin": ("carboxyhemoglobin",),
}


def matches_defect(row, tag):
    """True when this center's stated or assumed tests match that hazard."""
    needed = tests_for_defects([tag])
    if not needed:
        return False
    if _matching_stated(row.get("tests"), needed):
        return True
    for part in re.split(r",|;", row.get("assumed") or ""):
        kind = part.strip().lower()
        if not kind:
            continue
        aliases = _ASSUMED_FOR.get(kind, (kind,))
        if any(name in needed for name in aliases):
            return True
    return False


_NEEDS_CONTEXT = {"lead", "mold", "allergy", "blood lead"}
_TEST_CONTEXT = re.compile(r"\b(?:tests?|testing|screens?|screening|blood)\b", re.I)


def _tests_phrase(text):
    """Named tests the page actually states. Loose phrases are not kept.

    Lead, mold, and allergy count only next to a test or screening word,
    so a sentence about lead paint or a broken markdown link is not a test.
    """
    found = []
    page = text or ""
    for name in _NAMED_TESTS:
        match = re.search(r"\b" + re.escape(name) + r"\b", page, re.I)
        if not match:
            continue
        if name.lower() in _NEEDS_CONTEXT:
            window = page[max(0, match.start() - 48):match.end() + 48]
            if not _TEST_CONTEXT.search(window):
                continue
        if any(name.lower() in existing.lower() or existing.lower() in name.lower() for existing in found):
            continue
        found.append(name)
    return ", ".join(found)


def _matches_needed(tests, needed):
    return bool(_matching_stated(tests, needed))


def _matching_stated(tests, needed):
    kept = []
    for part in re.split(r",|;", tests or ""):
        stated = part.strip()
        if not stated:
            continue
        if any(re.search(r"\b" + re.escape(name) + r"\b", stated, re.I) for name in needed or []):
            kept.append(stated)
    return kept


def _excluded_site(name):
    return bool(_NOT_PRIMARY.search(name or ""))


def _is_primary_care(name, parent=""):
    """Primary care is read from this site's name. The parent cannot grant it."""
    if _excluded_site(name):
        return False
    if parent and _excluded_site(parent) and not _PRIMARY.search(name or ""):
        return False
    if _PRIMARY.search(name or ""):
        return True
    return bool(_HOSPITAL.search(name or "") and re.search(r"\b(?:primary care|pediatric|pediatrics|outpatient)\b", name or "", re.I))


def _is_hospital_or_emergency(name):
    """A general hospital or emergency department. Not a health center and not an outpatient clinic."""
    if _excluded_site(name):
        return False
    if _EMERGENCY.search(name or ""):
        return True
    if _HOSPITAL.search(name or "") and not _HOSPITAL_VISIT.search(name or ""):
        return True
    return False


def _assumed_tests(name, parent, needed, stated):
    """Blood tests this site type usually orders, when the page did not already state them."""
    assumed = []
    stated_text = ", ".join(stated)
    if "lead" in (needed or []) and _is_primary_care(name, parent) and not _matches_needed(stated_text, ["lead"]):
        assumed.append("blood lead")
    if _ALLERGY_SITE.search(name or "") and any(name_ in (needed or []) for name_ in ("mold", "allergy")):
        if not _matches_needed(stated_text, ["mold", "allergy"]):
            assumed.append("allergy")
    if "carboxyhemoglobin" in (needed or []) and _is_hospital_or_emergency(name):
        if not _matches_needed(stated_text, ["carboxyhemoglobin"]):
            assumed.append("carboxyhemoglobin")
    return assumed


def _filter_note(kept):
    if not kept:
        return ""
    assumed_lead = any("blood lead" in (row.get("assumed") or "") for row in kept)
    assumed_co = any("carboxyhemoglobin" in (row.get("assumed") or "") for row in kept)
    stated_screen = any(_matches_needed(row.get("tests"), ["lead", "mold", "allergy"]) for row in kept)
    def _parts(row):
        return [part.strip() for part in (row.get("assumed") or "").split(",")]

    name_allergy = any(part == "allergy" and not _is_npi(row) for row in kept for part in _parts(row))
    npi_allergy = any(part == "allergy" and _is_npi(row) for row in kept for part in _parts(row))
    npi_ent = any(part == "ent" and _is_npi(row) for row in kept for part in _parts(row))
    bits = []
    if assumed_lead:
        bits.append("primary care orders a blood lead test")
    if assumed_co:
        bits.append("hospital emergency departments order a carboxyhemoglobin blood test")
    if stated_screen:
        bits.append("the site states lead, mold, or allergy")
    elif name_allergy:
        bits.append("the center is an allergy clinic")
    if npi_allergy:
        bits.append("the NPI registry lists an allergy office near this ZIP")
    if npi_ent:
        bits.append("the NPI registry lists an ear, nose, and throat office near this ZIP")
    if any(row.get("assumed_insurance") for row in kept):
        bits.append("Medi-Cal or Medicare come from health-center rules or a public enrollment file")
    if not bits:
        return "Centers are shown because they match a test these defects need."
    return "Centers are shown because " + ", or because ".join(bits) + "."


def _needed_tests(tests, needed):
    kept = []
    for part in re.split(r",|;", tests or ""):
        stated = part.strip()
        if not stated:
            continue
        if any(re.search(r"\b" + re.escape(name) + r"\b", stated, re.I) for name in needed):
            kept.append(stated)
    return ", ".join(kept)


def _plans(text):
    found = []
    for name in PLAN_NAMES:
        match = re.search(r"\b" + re.escape(name) + r"\b", text or "", re.I)
        if not match:
            continue
        shown = match.group(0)
        if any(shown.lower() == existing.lower() for existing in found):
            continue
        found.append(shown)
    return ", ".join(found)


def _heading_names(text):
    """Site names from headings. The heading does not have to include an address."""
    found = []
    seen = set()
    for line in (text or "").splitlines():
        if not re.match(r"^#\s+\S", line):
            continue
        name = _clean_line(line)
        if not _SITE_WORD.search(name):
            continue
        if len(name) < 3 or len(name) > 80 or name.lower() in _SKIP_NAME:
            continue
        if name.endswith(".") and len(name) > 40:
            continue
        if not _contains(text, name) or _norm(name) in seen:
            continue
        seen.add(_norm(name))
        found.append(name)
        break
    return found


def _sites(text):
    """Name and address when both are on the page, otherwise the name alone."""
    sites = []
    seen = set()
    for name, address in _pairs(text):
        if not _contains(text, name) or not _contains(text, address):
            continue
        if _norm(name) in seen:
            continue
        seen.add(_norm(name))
        sites.append((name, address))
    for name in _heading_names(text):
        if _norm(name) in seen:
            continue
        seen.add(_norm(name))
        sites.append((name, ""))
    return sites


def address_from_text(text, zip_code=""):
    """First street address that this page actually states. Prefer the case ZIP."""
    found = []
    for line in (text or "").splitlines() or [text or ""]:
        match = _ADDRESS.search(_clean_line(line))
        if not match:
            continue
        address = re.sub(r"\s+", " ", match.group(1)).strip(" .,;")
        if address and _contains(text, address) and address not in found:
            found.append(address)
    for address in found:
        if zip_code and zip_code in address:
            return address
    for address in found:
        if "oakland" in address.lower():
            return address
    return found[0] if found else ""


def fill_address(row, page_text, zip_code=""):
    """Copy an address onto a named center only when that lookup page states it."""
    address = address_from_text(page_text, zip_code)
    if not address:
        return row
    row["address"] = address
    row["maps"] = _maps(None, None, address)
    return row


def clinics_from_pages(pages):
    """One clinic per site. A page may name the site without stating the address."""
    rows = []
    seen_names = set()
    for item in pages or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        text = str(item.get("text") or "")
        if not url.startswith("https://") or not text:
            continue
        sites = _sites(text)
        for name, address in sites:
            if _norm(name) in seen_names:
                continue
            seen_names.add(_norm(name))
            snippet = text if len(sites) == 1 or not address else _window(text, address)
            rows.append({
                "name": name,
                "address": address,
                "source_url": url,
                "page_text": snippet,
                "address_page_text": text if address and _contains(text, address) else "",
                "tests": _tests_phrase(snippet),
                "insurance": _plans(snippet),
            })
    clinics = parse_directory({"clinics": rows})
    note = "" if clinics else "No cited page listed a center name. Home does not invent clinic names."
    return {"clinics": clinics, "note": note}


def _host(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _allowed_host(url):
    return _host(url) in ALLOWED_HOSTS


def _links(text, base):
    found = []
    for match in re.finditer(r"https://[^\s)\]>\"']+", text or ""):
        found.append(match.group(0).rstrip(".,;"))
    for match in re.finditer(r"\[[^\]]*\]\((/[^)\s]+)\)", text or ""):
        found.append(urllib.parse.urljoin(base, match.group(1)))
    return found


def _website(raw):
    raw = str(raw or "").strip()
    if not raw or "@" in raw:
        return ""
    if not re.match(r"https?://", raw, re.I):
        raw = "https://" + raw
    host = _host(raw)
    if not host or "." not in host or host.startswith("wwww."):
        return ""
    return "https://" + host + "/"


def _share_family_sites(clinics):
    """A center with no website uses the site HRSA listed for the same parent."""
    by_parent = {}
    for clinic in clinics:
        parent = clinic.get("parent") or ""
        site = clinic.get("website") or ""
        if parent and site and parent not in by_parent:
            by_parent[parent] = site
    for clinic in clinics:
        parent = clinic.get("parent") or ""
        if not clinic.get("website") and parent in by_parent:
            clinic["website"] = by_parent[parent]


def apply_service_page(clinics, page_url, text, needed=None):
    """Copy tests, insurance, and funding from a page onto centers on that website.

    When needed is set, only tests that match those names are copied.
    """
    host = _host(page_url)
    if not host or not text:
        return 0
    tests = _tests_phrase(text)
    if needed is not None:
        tests = _needed_tests(tests, needed)
    insurance = _plans(text)
    funding = _funding(text)
    if not tests and not insurance and not funding:
        return 0
    copied = 0
    for clinic in clinics:
        if _host(clinic.get("website") or "") != host:
            continue
        if tests and not clinic.get("tests"):
            clinic["tests"] = tests
        if insurance and not clinic.get("insurance"):
            clinic["insurance"] = insurance
        if funding and not clinic.get("funding"):
            clinic["funding"] = funding
        if (tests or insurance) and str(page_url).startswith("https://"):
            clinic["source_url"] = page_url
        copied += 1
    return copied


def fill_services(clinics, read, limit=8, needed=None, search=None):
    """Read each center website once. Facts stay empty when the page does not state them.

    When needed tests and a search function are passed, one site search is used
    instead of the homepage, and only those tests are copied.
    """
    _share_family_sites(clinics)
    hosts = []
    for clinic in clinics:
        host = _host(clinic.get("website") or "")
        if host and host not in hosts:
            hosts.append(host)
    for host in hosts[:limit]:
        if needed and search:
            quoted = " OR ".join(f'"{name}"' for name in needed)
            pages = search(f"site:{host} ({quoted})") or []
            for page in pages[:1]:
                apply_service_page(clinics, page.get("url") or ("https://" + host + "/"), page.get("text") or "", needed)
            continue
        url = "https://" + host + "/"
        text = read(url) or ""
        if text:
            apply_service_page(clinics, url, text, needed)
    return clinics


def _capped_research(token, limit):
    from agents.legal.research import Research

    class Capped(Research):
        def __init__(self, apify_token, call_limit):
            super().__init__(apify_token)
            self.limit = call_limit
            self.calls = 0

        def _rag(self, query, max_results, scraping_tool="raw-http"):
            key = (query, max_results, scraping_tool)
            if key in self._cache:
                return self._cache[key]
            if self.calls >= self.limit:
                return []
            self.calls += 1
            return super()._rag(query, max_results, scraping_tool)

    return Capped(token, limit)


def lookup_services(clinics, token, limit=8, needed=None):
    """Apify read of each center website. Does not invent tests or insurance.

    With needed test names, one site-restricted search per website copies a
    test only when that page states it and it is one of those names.
    """
    web = _capped_research(token, limit)

    def read(url):
        kind, pages = web.read(url)
        if kind != "pages":
            return ""
        return "\n".join(page.get("text") or "" for page in pages)

    def search(query):
        return web.search(query, max_results=1)

    return fill_services(clinics, read, limit, needed=needed, search=search if needed else None)


def load_env():
    """Same .env load as agents/health/agent.py. Does not override existing vars."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def apify_token():
    """Property token, else the bare APIFY_TOKEN. Never reads APIFY_TOKEN_Legal."""
    load_env()
    return os.environ.get("APIFY_TOKEN_Property") or os.environ.get("APIFY_TOKEN") or ""


def fetch_cited(zip_code, token):
    """HRSA read, one search, up to two page reads, then up to two address lookups.

    The cap is 6 actor calls. A cited page may contain only the center name.
    The street address is copied from a later page when that page states it.
    """
    web = _capped_research(token, 6)
    pages = []
    seen = set()

    def add(items, linked_ok=False):
        for item in items or []:
            url = item.get("url") or ""
            if not url.startswith("https://") or url in seen:
                continue
            if not (_allowed_host(url) or linked_ok):
                continue
            seen.add(url)
            pages.append(item)

    hrsa = search_url(zip_code)
    calls_before = web.calls
    kind, got = web.read(hrsa)
    if kind == "pages":
        add(got)
    hrsa_text = "\n".join(
        item.get("text") or "" for item in pages if "findahealthcenter.hrsa.gov" in (item.get("url") or "")
    )
    # read() only retries with playwright when the first result is empty. A title-only
    # page has no center names, and that retry is allowed for this one URL.
    if not _pairs(hrsa_text) and web.calls == calls_before + 1 and web.calls < web.limit:
        add(web._rag(hrsa, 1, "browser-playwright"))

    if web.calls < web.limit:
        found = web.search(
            'free OR "no-cost" OR "city-funded" testing clinic '
            'site:health.alamedacountyca.gov OR site:acphd.org OR site:oaklandca.gov',
            max_results=3,
        )
        add(found)

    linked = []
    for item in list(pages):
        if not _allowed_host(item.get("url") or ""):
            continue
        for url in _links(item.get("text") or "", item["url"]):
            if url.startswith("https://") and url not in seen and url not in linked:
                linked.append(url)
    linked.sort(key=lambda url: 0 if _allowed_host(url) else 1)

    reads = 0
    for url in linked:
        if reads >= 2 or web.calls >= web.limit:
            break
        got = web._rag(url, 1)
        reads += 1
        add(got, linked_ok=True)
    directory = clinics_from_pages(pages)
    lookups = 0
    for row in directory["clinics"]:
        if row.get("address") or lookups >= 2 or web.calls >= web.limit:
            continue
        found = web.search(f'"{row["name"]}" address Oakland {zip_code}', max_results=2)
        lookups += 1
        for page in found:
            filled = fill_address(row, page.get("text") or "", zip_code)
            if filled.get("address"):
                break
    return directory


def cited_path(zip_code):
    safe = re.sub(r"[^0-9A-Za-z_-]", "", zip_code or "") or "unknown"
    return ROOT / "tenant" / "out" / f"clinics_{safe}.json"


def write_cited(zip_code, directory):
    path = cited_path(zip_code)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(directory, indent=2) + "\n", encoding="utf-8")
    return path


def cited_directory(zip_code):
    """Saved cited file, else one live fetch when HOME_CLINICS_LIVE=1 and a token is set."""
    path = cited_path(zip_code)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            return data
    if os.environ.get("HOME_CLINICS_LIVE") != "1":
        return None
    token = apify_token()
    if not token:
        return None
    try:
        data = fetch_cited(zip_code, token)
    except Exception:
        return None
    write_cited(zip_code, data)
    return data
