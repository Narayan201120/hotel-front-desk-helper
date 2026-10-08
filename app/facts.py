"""Load and serve the structured fact sheet.

The YAML file is the single source of truth. The model may phrase answers,
but every number, time, or fee in a reply must already exist here.
"""
from __future__ import annotations

from pathlib import Path

import yaml

FACT_PATH = Path(__file__).resolve().parent.parent / "data" / "hotel_facts.yaml"


class FactSheet:
    def __init__(self, path: Path = FACT_PATH) -> None:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self.hotel = raw.get("hotel", {}) or {}
        self.facts = {f["id"]: f for f in raw.get("facts", []) or []}

    def get(self, fact_id: str) -> dict | None:
        return self.facts.get(fact_id)

    def answer_text(self, fact_id: str) -> str | None:
        fact = self.get(fact_id)
        return fact["answer"] if fact else None

    def all_values_entries(self) -> list[str]:
        entries = list(self.hotel.get("values", []) or [])
        for fact in self.facts.values():
            entries.extend(fact.get("values", []) or [])
        return entries
