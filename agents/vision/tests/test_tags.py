import json
import re

import agent
import rules

ROOT = rules.ROOT


def ts_hazards():
    src = (ROOT / "spacetimedb" / "src" / "index.ts").read_text(encoding="utf-8")
    block = re.search(r"const HAZARDS = new Set\(\[(.*?)\]\)", src, re.S).group(1)
    return re.findall(r"'([a-z_]+)'", block)


def contract_tags():
    md = (ROOT / "docs" / "contracts.md").read_text(encoding="utf-8")
    block = re.search(r"## 1\. Hazard tags.*?```(.*?)```", md, re.S).group(1)
    return [t.strip() for t in block.replace("\n", " ").split(",") if t.strip()]


def pack():
    return json.loads((ROOT / "data" / "packs" / "ca_oakland.json").read_text(encoding="utf-8"))


def test_tags_match_spacetimedb_allowlist():
    assert sorted(rules.TAGS) == sorted(ts_hazards())


def test_tags_match_contract_section_1():
    assert sorted(rules.TAGS) == sorted(contract_tags())


def test_tool_enum_is_the_tag_list():
    enum = agent.TOOL["input_schema"]["properties"]["hazards"]["items"]["properties"]["hazard"]["enum"]
    assert enum == rules.TAGS


def test_every_tag_resolves_to_pack_entry():
    p = pack()
    for tag in rules.TAGS:
        legal = rules.legal_for(tag, p)
        assert "status" not in legal, tag
        assert legal["citations"], tag
        assert all(c["source_url"].startswith("https://") for c in legal["citations"])


def test_same_as_resolution():
    legal = rules.legal_for("cockroaches", pack())
    assert legal["resolved_from"] == "rodents"
    assert legal["citations"][0]["cite"].startswith("Cal. Civil Code 1941.1(a)(6)")


def test_unknown_tag_and_missing_pack():
    assert rules.legal_for("asbestos", pack()) == {"status": "needs_legal_review"}
    assert rules.legal_for("water_leak", None) == {"status": "no_pack_for_jurisdiction"}
    assert rules.load_pack({"state": "NY", "city": "New York"}) is None
    assert rules.load_pack({"state": "CA", "city": "Oakland"})["pack_id"] == "ca-oakland"


def test_never_say_covers_every_pack_line():
    assert set(rules.NEVER_SAY_PATTERNS) == set(pack()["never_say"])


def test_every_tag_has_definition_rubric_label():
    for tag in rules.TAGS:
        assert rules.DEFINITIONS[tag] and rules.RUBRIC[tag] and rules.LABELS[tag]
        assert tag in agent.SYSTEM
    assert "mold" not in rules.LABELS["dampness_mold"].lower().replace("possible mold", "")


def test_urgent_tags_match_health_agent():
    from urgent import URGENT
    assert rules.URGENT_TAGS == set(URGENT)
