"""Nearby testing sites. A directory response is parsed; nothing is invented.

With no directory payload, the only result is the HRSA search page for the ZIP.
"""
import urllib.parse

HRSA = "https://findahealthcenter.hrsa.gov/"


def search_url(zip_code):
    if zip_code:
        return HRSA + "?" + urllib.parse.urlencode({"zip": zip_code})
    return HRSA


def parse_directory(payload):
    """Accept {clinics: [...]} or a Places-style {results: [...]}."""
    rows = []
    if isinstance(payload, dict):
        rows = payload.get("clinics") or payload.get("results") or []
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
        if lat is not None and lon is not None:
            maps = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(f"{lat},{lon}")
        elif address:
            maps = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(address)
        else:
            maps = ""
        distance = item.get("distance_mi")
        out.append({
            "name": name,
            "address": address,
            "distance_mi": distance,
            "phone": str(item.get("phone") or "").strip(),
            "maps": maps,
        })
    return out


def lookup(zip_code, see_a_doctor_if, directory=None):
    url = search_url(zip_code or "")
    reason = ""
    for line in see_a_doctor_if or []:
        text = line.get("text") if isinstance(line, dict) else str(line)
        if text:
            reason = text
            break
    if not directory:
        return {
            "source": "hrsa_link",
            "note": "No clinic directory key is set. This opens the HRSA health-center search for your ZIP. Home does not invent clinic names.",
            "reason": reason,
            "search_url": url,
            "clinics": [],
        }
    return {
        "source": "directory",
        "note": "",
        "reason": reason,
        "search_url": url,
        "clinics": parse_directory(directory),
    }
