"""Load the existing agents' pure helpers without importing their network code."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RULES = load("vision_rules", ROOT / "agents" / "vision" / "rules.py")
URGENT = load("health_urgent", ROOT / "agents" / "health" / "urgent.py")
LEGAL = load("legal_validate", ROOT / "agents" / "legal" / "validate.py")
