"""Cited plan facts only. A square footage or a plan URL is kept when its snippet is in the fetched page.

Host checks match agents/property/sources.py: public https hostnames only, no IP and no localhost.
"""
import ipaddress
import re
import urllib.parse

FACTS = {"plan_url", "bedrooms", "bathrooms", "sqft", "assessor_sqft", "room_label"}
DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9-]+\.)+[a-z]{2,}$")


def public_https(url):
    """An https URL on a public hostname."""
    parts = urllib.parse.urlsplit((url or "").strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not DOMAIN.match(host) or host.endswith(".local") or host.endswith(".internal"):
        return ""
    try:
        ipaddress.ip_address(host)
        return ""
    except ValueError:
        pass
    return urllib.parse.urlunsplit(("https", host, parts.path, parts.query, ""))


def keep_facts(facts, results):
    """Return (kept, dropped). A fact's snippet must appear in the result it cites."""
    kept = []
    dropped = []
    for fact in facts or []:
        kind = str(fact.get("kind") or "")
        data_id = str(fact.get("data_id") or "")
        snippet = str(fact.get("snippet") or "").strip()
        value = str(fact.get("value") or "").strip()
        reason = None
        if kind not in FACTS:
            reason = "unknown kind"
        elif data_id not in results:
            reason = "unknown data_id"
        elif not snippet or snippet not in results[data_id]:
            reason = "snippet is not in the cited page"
        elif kind == "plan_url" and not public_https(value):
            reason = "plan url is not public https"
        elif kind in {"sqft", "assessor_sqft", "bedrooms", "bathrooms"}:
            try:
                number = float(value)
            except ValueError:
                number = None
            if number is None or number <= 0:
                reason = "value is not a positive number"
            elif str(int(number)) not in results[data_id] and value not in results[data_id]:
                reason = "value is not in the cited page"
        if reason:
            dropped.append({"kind": kind, "value": value, "reason": reason})
            continue
        kept.append({"kind": kind, "value": value, "data_id": data_id, "snippet": snippet})
    return kept, dropped
