"""Self-test: every fact's own answer text must pass the guardrail.

The sheet is the allow-list source, so its own texts are the minimum bar.
Run: py tests/facts_selftest.py
Exit code 1 on any failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.facts import FactSheet  # noqa: E402
from app.guardrails import build_allowed, check_reply  # noqa: E402


def main() -> int:
    sheet = FactSheet()
    allowed = build_allowed(sheet.all_values_entries())
    failures = []
    for fid, fact in sheet.facts.items():
        ok, reason = check_reply(fact["answer"], allowed)
        print(f"{'ok' if ok else 'FAIL'}: {fid} ({reason})", flush=True)
        if not ok:
            failures.append(fid)
    print(f"{len(sheet.facts) - len(failures)}/{len(sheet.facts)} fact texts pass.", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
