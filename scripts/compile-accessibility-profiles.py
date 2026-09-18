#!/usr/bin/env python3
"""Embed reviewed accessibility profiles; application plans cannot add patches."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wechattool.accessibility import validate_document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Generated native include file")
    args = parser.parse_args()
    document = validate_document(json.loads((ROOT / "wechattool/accessibility_profiles.json").read_text()))
    payload = json.dumps(document, ensure_ascii=True, separators=(",", ":"))
    args.output.write_text(
        "// Generated from reviewed source profiles; never read from an app plan.\n"
        "static const char kCompiledAccessibilityProfiles[] = " + json.dumps(payload) + ";\n"
    )


if __name__ == "__main__":
    main()
