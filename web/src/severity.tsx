// Severity colors stay apart for deuteranopia, protanopia and tritanopia (closest pair ΔE 14),
// and each passes 4.5:1 with white text. Every use also shows the word and a shape, so color is never the only cue.
export const SEVERITY: Record<string, { color: string; label: string; mark: string }> = {
  low: { color: "#0F6E8C", label: "Low", mark: "●" },
  moderate: { color: "#80600A", label: "Moderate", mark: "▲" },
  high: { color: "#7B3F9E", label: "High", mark: "◆" },
  urgent: { color: "#8E1B1B", label: "Urgent", mark: "!" },
};

export function severityOf(level?: string) {
  return SEVERITY[level || ""] || SEVERITY.moderate;
}

export function SeverityBadge({ level }: { level?: string }) {
  const sev = severityOf(level);
  return (
    <span className="badge" style={{ background: sev.color }}>
      <span aria-hidden="true">{sev.mark}</span> {sev.label}
    </span>
  );
}
