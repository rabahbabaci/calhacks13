"""One-page PDF of a form packet: the draft, the official link, and the filing steps. No extra library."""


def _escape(text):
    return (text or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap(text, width=90):
    words = (text or "").split()
    lines, cur = [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if len(trial) > width:
            if cur:
                lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines or [""]


def build_pdf(title, filled, still_needed, steps, form_url, form_name):
    """Return PDF bytes. The form URL is one unbroken line so a test can find it."""
    lines = ["DRAFT", title, ""]
    lines.append("Filled from your case")
    for item in filled:
        lines.extend(_wrap(f"{item['label']}: {item['value']}"))
    lines.append("")
    lines.append("You still complete")
    for item in still_needed:
        lines.extend(_wrap(f"- {item}"))
    lines.append("")
    lines.append(form_name or "Official form")
    lines.append(form_url or "No separate form page. See the statute link in the steps.")
    lines.append("")
    lines.append("How to file")
    for i, step in enumerate(steps, start=1):
        lines.extend(_wrap(f"{i}. {step}"))
    lines.append("")
    lines.append("You file this yourself. Goldilocks does not submit it.")

    ops = ["BT", "/F1 11 Tf", "50 760 Td", "14 TL"]
    for line in lines:
        ops.append(f"({_escape(line)}) Tj")
        ops.append("T*")
    ops.append("ET")
    stream = ("\n".join(ops) + "\n").encode("latin-1", "replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{i} 0 obj\n".encode("ascii"))
        body.extend(obj)
        body.extend(b"\nendobj\n")
    xref_at = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    body.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        body.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    body.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode("ascii")
    )
    return bytes(body)
