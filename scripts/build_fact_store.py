"""Build the policy fact store from the brochure PDFs.

Usage (from the project folder, venv active):
  python scripts/build_fact_store.py                 # all 4 policies (4 AI calls)
  python scripts/build_fact_store.py --policy CARE   # rebuild one policy only
  python scripts/build_fact_store.py --policy CARE --no-fallback   # strong model only, never Flash Lite
  python scripts/build_fact_store.py --validate      # apply fact_overrides.json + re-check (no AI calls)
  python scripts/build_fact_store.py --ingest-only   # just extract text + chunks (no AI calls)
"""
import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, fact_store, ingest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
for noisy in ("httpx", "google_genai", "google"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


def print_summary(facts, codes):
    print("\nFACT STORE SUMMARY")
    print(f"{'Policy':<8}{'Facts':>7}{'Auto-OK':>9}{'Human-OK':>10}{'Review':>8}")
    for code in codes:
        counts = Counter(f["status"] for f in facts if f["policy"] == code)
        total = sum(counts.values())
        print(f"{code:<8}{total:>7}{counts['auto_verified']:>9}{counts['human_verified']:>10}{counts['needs_review']:>8}")

    lite = Counter(f["policy"] for f in facts if config.GEMINI_MODEL_STRONG and f.get("extracted_by")
                   and f["extracted_by"] != config.GEMINI_MODEL_STRONG)
    for code, n in sorted(lite.items()):
        print(f"  note: {n} {code} facts came from the fallback model — consider: --policy {code} --no-fallback")

    review = [f for f in facts if f["policy"] in codes and f["status"] == "needs_review"]
    if review:
        print(f"\nNEEDS REVIEW ({len(review)}) — check these against the PDF:")
        for f in review:
            print(f"  {f['fact_id']} (p{f['page']}): {f['value']}  ->  {'; '.join(f['flags'])}")


def print_golden(facts, codes):
    results = fact_store.run_golden_checks(facts, codes)
    if not results:
        return True
    print("\nGOLDEN CHECKS (known traps)")
    for r in results:
        print(f"  [{r['result']:<7}] {r['policy']}: {r['why']}")
        if r["result"] != "PASS":
            for found in r["found"] or ["no matching fact"]:
                print(f"             found: {found}")
    return all(r["result"] == "PASS" for r in results)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--policy", action="append", choices=list(config.POLICIES),
                        help="Policy code to rebuild (repeatable). Default: all.")
    parser.add_argument("--validate", action="store_true", help="Re-run code checks only, no AI calls.")
    parser.add_argument("--ingest-only", action="store_true", help="Only extract text and chunks.")
    parser.add_argument("--no-fallback", action="store_true",
                        help="Use only the strong model; fail instead of falling back to the lite model.")
    args = parser.parse_args()
    codes = args.policy or list(config.POLICIES)

    print("Step 1/3  Reading PDFs")
    try:
        report = ingest.ingest_all(codes)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(f"ERROR: {e}")
    for code, r in report.items():
        heavy = f", image-heavy pages: {r['image_heavy_pages']}" if r["image_heavy_pages"] else ""
        print(f"  {code}: {r['pages']} pages, {r['chunks']} chunks{heavy}")
    if args.ingest_only:
        print(f"\nSaved {config.PAGES_FILE.name} and {config.CHUNKS_FILE.name}")
        return

    pages = ingest.load_pages()
    if args.validate:
        print("Step 2/3  Skipped AI extraction (--validate)")
        try:
            facts, errors = fact_store.revalidate(pages)
        except (ValueError, json.JSONDecodeError) as e:
            sys.exit(f"ERROR in fact_overrides.json: {e}")
    else:
        if config.DEMO_MODE:
            sys.exit("ERROR: No GEMINI_API_KEY in .env (or DEMO_MODE=true). Extraction needs the API.")
        print(f"Step 2/3  Extracting facts with {config.GEMINI_MODEL_STRONG} "
              f"({len(codes)} AI call(s), ~15s apart for the free-tier limit)")
        try:
            facts, errors = fact_store.build(codes, pages, fallback=not args.no_fallback)
        except (ValueError, json.JSONDecodeError) as e:
            sys.exit(f"ERROR in fact_overrides.json: {e}")
    for code, message in errors.items():
        if code == "overrides":
            print(f"  WARNING fact_overrides.json: {message}")
        else:
            print(f"  FAILED {code}: {message}  (previous facts for {code} kept)")

    print("Step 3/3  Checking facts against the PDF text")
    all_codes = sorted({f["policy"] for f in facts})
    print_summary(facts, all_codes)
    golden_ok = print_golden(facts, all_codes)

    print(f"\nSaved: {config.FACTS_FILE.relative_to(config.ROOT)}")
    print(f"Review sheet: {config.FACTS_REVIEW_CSV.relative_to(config.ROOT)} (open in Excel)")
    if any(code != "overrides" for code in errors) or not golden_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()