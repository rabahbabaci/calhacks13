"""Fixed rules for the vision agent. No LLM. Applied in code after the model call.

Tags are the nine contract tags (docs/contracts.md section 1). Legal text comes only from the
jurisdiction pack; the model never writes it.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKS = ROOT / "data" / "packs"

TAGS = [
    "dampness_mold", "water_leak", "rodents", "cockroaches", "exposed_wiring",
    "lead_paint", "inadequate_heat", "carbon_monoxide", "gas_leak",
]
URGENT_TAGS = {"exposed_wiring", "gas_leak", "carbon_monoxide"}
SEVERITIES = ["low", "moderate", "high", "urgent"]
EXTENTS = ["spot", "patch", "widespread"]

LABELS = {
    "dampness_mold": "Visible dampness or possible mold growth",
    "water_leak": "Water leak or water damage",
    "rodents": "Signs of rodents",
    "cockroaches": "Signs of cockroaches",
    "exposed_wiring": "Exposed or damaged wiring",
    "lead_paint": "Peeling or deteriorated paint (possible lead hazard)",
    "inadequate_heat": "Heating problem",
    "carbon_monoxide": "Carbon monoxide alarm problem",
    "gas_leak": "Possible gas leak",
}

# Short definitions for the system prompt, taken from the pack excerpts and cautions.
DEFINITIONS = {
    "dampness_mold": "Dampness of habitable rooms, or visible growth that may be mold (dark spotting, fuzzy patches, "
                     "staining with growth). Minor mold on surfaces meant to get wet, like shower grout or a tub edge, "
                     "is excluded as a finding: report it only as low severity and say it is a wet surface. "
                     "Only a health or code officer can determine mold, so never assert that something is mold.",
    "water_leak": "Failed waterproofing or plumbing: water stains, active drips, sagging or bubbling ceilings, "
                  "pooled water, water entering through roof, walls, windows or doors.",
    "rodents": "Signs of rats or mice: droppings, gnaw marks, nests, burrows, live or dead animals.",
    "cockroaches": "Signs of cockroaches: live or dead insects, droppings, egg cases, shed skins.",
    "exposed_wiring": "Wiring or electrical equipment not in safe working order: bare or frayed wires, open junction "
                      "boxes, missing outlet or switch covers, scorch marks, hanging fixtures.",
    "lead_paint": "Deteriorated paint (peeling, chipping, chalking, cracking) that could be a lead hazard. "
                  "Peeling paint alone does not prove lead; never say lead is present.",
    "inadequate_heat": "Heating facilities not in good working order. Usually not visible. Report only from visible "
                       "evidence (broken or missing heater, disconnected radiator) or tenant notes.",
    "carbon_monoxide": "Missing or broken carbon monoxide alarm, or visible signs of a faulty fuel-burning appliance. "
                       "CO itself is invisible.",
    "gas_leak": "Gas facilities not in safe order: damaged gas lines or fittings, disconnected appliance gas lines. "
                "A leak itself is usually not visible.",
}

RUBRIC = {
    "dampness_mold": "spot = low, patch = moderate, widespread or on a ceiling or in a bedroom = high.",
    "water_leak": "stain = low, active drip or sagging = high.",
    "rodents": "one sign = low, multiple signs or live animals = high.",
    "cockroaches": "one sign = low, multiple signs or live insects = high.",
    "exposed_wiring": "always urgent, report even at low confidence.",
    "lead_paint": "peeling paint only = moderate, always needs_professional_check.",
    "inadequate_heat": "only from visible evidence or tenant notes, otherwise list it in not_visible_from_video.",
    "carbon_monoxide": "always urgent, report even at low confidence.",
    "gas_leak": "always urgent, report even at low confidence.",
}

# One entry per never_say line in the pack; tests check the keys match the pack exactly.
NEVER_SAY_PATTERNS = {
    "You can withhold rent / stop paying rent": [r"\bwithhold(ing)? (the |your )?rent\b", r"\bstop paying (the |your )?rent\b"],
    "You have a case / you will win / you could get $X": [r"\byou have a (strong |good )?case\b", r"\byou will win\b", r"\byou could get \$"],
    "The landlord must fix this within N days (unless a section above states it)": [r"\bmust (fix|repair)\b[^.]{0,60}\bwithin \d+ days\b"],
    "This is mold (as a finding)": [r"\b(this|it|that) is (black |toxic )?mold\b", r"\bmold is present\b", r"\bconfirmed mold\b", r"\btoxic mold\b"],
    "Court-ready": [r"\bcourt[- ]ready\b"],
}
VISION_BANNED = {
    "lead_paint": [r"\b(contains|confirmed|is) lead\b", r"\blead is present\b"],
}

TEXT_FIELDS = ["location", "severity_reason", "check_reason"]
# Reasons for tags with no finding, written in code instead of by the model.
NOT_SEEN = {
    "carbon_monoxide": "CO is invisible; check for a working CO alarm",
    "gas_leak": "a leak is usually not visible; leave and call for help if you smell gas",
    "inadequate_heat": "heating cannot be judged from video; describe it in the notes",
}
# Pack caution: minor mold on surfaces meant to get wet (like a shower) is excluded.
WET_SURFACE = re.compile(r"\b(shower|tub|bathtub|grout)\b")


def load_pack(jurisdiction):
    state = (jurisdiction or {}).get("state", "").lower()
    city = (jurisdiction or {}).get("city", "").lower().replace(" ", "_")
    path = PACKS / f"{state}_{city}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def legal_for(hazard, pack):
    """Pack entry for a hazard, resolving same_as. The model never writes any of this."""
    if pack is None:
        return {"status": "no_pack_for_jurisdiction"}
    entries = {e["hazard"]: e for e in pack["hazard_laws"]}
    entry = entries.get(hazard)
    if entry is None:
        return {"status": "needs_legal_review"}
    resolved_from = None
    if "same_as" in entry:
        resolved_from = entry["same_as"]
        entry = entries[resolved_from]
    return {
        "pack_id": pack["pack_id"],
        "resolved_from": resolved_from,
        "citations": [{"cite": c["cite"], "excerpt": c["excerpt"], "source_url": c["source_url"]} for c in entry["citations"]],
        "landlord_duty": entry["landlord_duty"],
        "cautions": entry.get("cautions", []),
        "disclaimer": pack["disclaimer"],
    }


def never_say_hit(hazard, texts):
    """First banned phrase found in any of the texts, or None."""
    blob = " ".join(t for t in texts if t).lower()
    for line, patterns in NEVER_SAY_PATTERNS.items():
        for p in patterns:
            if re.search(p, blob):
                return f"never_say: {line}"
    for p in VISION_BANNED.get(hazard, []):
        if re.search(p, blob):
            return f"banned for {hazard}: /{p}/"
    return None


def _rank(severity):
    return SEVERITIES.index(severity) if severity in SEVERITIES else 0


def max_severity(*sevs):
    return max(sevs, key=_rank)


def apply_rubric(h):
    """Raise-only floors from the rubric, plus the fixed urgent and lead rules. Mutates and returns h."""
    tag = h["hazard"]
    sev = h["severity"] if h.get("severity") in SEVERITIES else "low"
    if tag in URGENT_TAGS:
        sev = "urgent"
        h["needs_professional_check"] = True
        h["check_reason"] = h.get("check_reason") or "Possible safety emergency; a qualified professional must inspect."
    elif tag == "dampness_mold":
        where = f"{h.get('location', '')} {h.get('room', '')}".lower()
        h["wet_surface"] = bool(WET_SURFACE.search(where)) and "ceiling" not in where
        if "ceiling" in where or "bedroom" in where:
            sev = max_severity(sev, "high")
        elif h["wet_surface"]:
            sev = "moderate" if h.get("extent") == "widespread" else "low"
        elif h.get("extent") == "widespread":
            sev = max_severity(sev, "high")
        elif h.get("extent") == "patch":
            sev = max_severity(sev, "moderate")
    elif tag == "lead_paint":
        sev = "moderate"
        h["needs_professional_check"] = True
        h["check_reason"] = h.get("check_reason") or "Only a lead inspection can tell whether this paint contains lead."
    h["severity"] = sev
    return h
