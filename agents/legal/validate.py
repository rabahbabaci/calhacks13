"""Code-enforced rules for legal claims. Pure functions, no network.

A claim is {"text": str, "source_ids": [str]}. Source ids are P# (verified
pack citations), G# (pack general rules), C# (pack city procedures) and R#
(sources the agent researched and saved with a checked excerpt).

A claim is dropped, not rewritten, when it:
- has no text or cites no known source;
- says something the jurisdiction pack lists under never_say (rent withholding,
  promised outcomes or money, "court-ready", calling something mold);
- states a number of days that none of its cited sources mention;
- is a similar case that cites no court opinion or reported settlement.
Claims that rest on a source the pack marks "legal_aid_question" or
"eligibility_check_needed" are moved to ask_legal_aid instead of being shown
as something the tenant can simply do.
"""
import re

CLAIM_FIELDS = ["landlord_duty", "tenant_options", "deadlines", "ask_legal_aid", "similar_cases"]

NEVER_SAY = [
    (re.compile(r"\byou\b[^.]{0,40}\b(can|may|could|should)\b[^.]{0,40}\b(withhold|stop paying|not pay)\b", re.I), "rent withholding advice"),
    (re.compile(r"\b(you have a (strong |good )?case|you will win|win your case|you('ll| will) get|you could get|you are entitled to)\b", re.I), "promised outcome"),
    (re.compile(r"\bcourt[- ]ready\b", re.I), "court-ready"),
    (re.compile(r"\b(this|it) is (black |toxic )?mold\b|\bconfirmed mold\b|\byou have mold\b", re.I), "mold stated as a finding"),
]
MONEY = re.compile(r"\$\s?\d")
DAYS = re.compile(r"\b(\d{1,3})(?:\s*|-)(?:calendar |business )?days?\b", re.I)
ASK_LEGAL_AID = {"legal_aid_question", "eligibility_check_needed"}


def check_claim(field, claim, sources):
    """Return (clean_claim, None) or (None, reason)."""
    text = str(claim.get("text", "")).strip()
    ids = [str(i).strip() for i in claim.get("source_ids") or [] if str(i).strip()]
    if not text:
        return None, "empty text"
    if not ids:
        return None, "no source_ids"
    unknown = [i for i in ids if i not in sources]
    if unknown:
        return None, f"unknown source ids {unknown}"
    for pattern, reason in NEVER_SAY:
        if pattern.search(text):
            return None, f"never_say: {reason}"
    if MONEY.search(text) and field != "similar_cases":
        return None, "never_say: money amount outside a reported case"
    cited_text = " ".join(sources[i].get("text") or "" for i in ids)
    for days in DAYS.findall(text):
        if not re.search(rf"\b{days}\b", cited_text):
            return None, f"{days} days not stated in the cited sources"
    if field == "similar_cases" and not any(sources[i].get("kind") in ("case_law", "settlement") for i in ids):
        return None, "similar case cites no court opinion or settlement"
    return {"text": text, "source_ids": sorted(set(ids), key=ids.index)}, None


def validate(hazard, draft, sources):
    """Apply the rules to a drafted assessment. Returns (fields, cited_ids, dropped)."""
    out = {f: [] for f in CLAIM_FIELDS}
    dropped = []
    for field in CLAIM_FIELDS:
        for claim in draft.get(field) or []:
            clean, reason = check_claim(field, claim, sources)
            if reason:
                dropped.append({"hazard": hazard, "field": field, "text": str(claim.get("text", "")), "reason": reason})
                continue
            target = field
            if field == "tenant_options" and any(sources[i].get("show_as") in ASK_LEGAL_AID for i in clean["source_ids"]):
                target = "ask_legal_aid"
            out[target].append(clean)
    cited = []
    for field in CLAIM_FIELDS:
        for claim in out[field]:
            for i in claim["source_ids"]:
                if i not in cited:
                    cited.append(i)
    return out, cited, dropped
