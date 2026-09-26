"""Audit a saved pitch and write the report (JSON, CSV, HTML) to outputs/audits/.

Usage (from the project folder, venv active):
  python scripts/run_audit.py                         # audits the most recent pitch in outputs/pitches/
  python scripts/run_audit.py outputs/pitches/X.json  # audits a specific pitch
  python scripts/run_audit.py --no-ai                 # code checks only (no AI call)
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import audit, config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
for noisy in ("httpx", "google_genai", "google"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pitch", nargs="?", help="Path to a pitch JSON file (default: newest in outputs/pitches/)")
    parser.add_argument("--no-ai", action="store_true", help="Run only the code checks")
    args = parser.parse_args()

    if args.pitch:
        path = Path(args.pitch)
    else:
        pitches = sorted((config.OUTPUT_DIR / "pitches").glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not pitches:
            sys.exit("No saved pitches in outputs/pitches/. Generate one in the app first.")
        path = pitches[-1]
    if not path.exists():
        sys.exit(f"Not found: {path}")

    pitch = json.loads(path.read_text(encoding="utf-8"))
    print(f"Auditing {path.name} ({pitch.get('company')}, recommended {pitch.get('recommended')})")
    report = audit.audit_pitch_content(pitch["slides"], pitch["policies"], profile=pitch.get("profile"),
                                       ranking=pitch.get("ranking"), pitch_meta=pitch, use_ai=not args.no_ai)
    paths = audit.save_report(report)

    s = report["summary"]
    print(f"\n{s['overall']}  grounding score {s['grounding_score']}%  "
          f"(verified {s['counts']['verified']}, review {s['counts']['review']}, fail {s['counts']['fail']}, info {s['counts']['info']})")
    print(s["headline"])
    for c in report["claims"]:
        if c["status"] in ("fail", "review"):
            print(f"  {c['status'].upper():7} {c['id']:6} {c['text'][:70]}\n          -> {c['reason']}")
    print("\nReport files:")
    for kind, p in paths.items():
        print(f"  {kind:5} {p}")


if __name__ == "__main__":
    main()