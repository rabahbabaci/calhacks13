"""Validate an action draft. Every section cites a source the agents already stored, or it is dropped.

The form URL and the filing steps are not part of the draft. Code attaches those from the pack.
"""
from tenant.refs import LEGAL

check_claim = LEGAL.check_claim


def _banned(text):
    """A subject is a label, not a cited claim. Drop it only when the legal never_say rules hit."""
    probe = {"s": {"text": text, "kind": "other"}}
    _clean, reason = check_claim("tenant_options", {"text": text, "source_ids": ["s"]}, probe)
    if reason and (reason.startswith("never_say") or "money" in reason):
        return reason
    return None


def validate_draft(draft, sources):
    """draft: {subject, sections: [{text, source_ids}]}. sources: id -> {text, kind, show_as?}.

    Returns {subject, sections, dropped}.
    """
    subject = str((draft or {}).get("subject") or "").strip()
    dropped = []
    if subject:
        reason = _banned(subject)
        if reason:
            dropped.append({"field": "subject", "text": subject, "reason": reason})
            subject = ""
    sections = []
    for section in (draft or {}).get("sections") or []:
        clean, reason = check_claim("tenant_options", section, sources)
        if reason:
            dropped.append({"field": "sections", "text": str((section or {}).get("text", "")), "reason": reason})
            continue
        sections.append(clean)
    return {"subject": subject, "sections": sections, "dropped": dropped}
