"""PDF ingestion: page text, page images, and clause-sized chunks for search.

Outputs (via ingest_all):
  data/processed/pages.json   full cleaned text of every page, used to verify quotes
  data/processed/chunks.json  ~90-word chunks with stable IDs, used for BM25 search
"""
import json
import re
import unicodedata
from pathlib import Path

import pymupdf # PyMuPDF (pip package name: pymupdf)

from src import config

CHUNK_TARGET_WORDS = 90
IMAGE_HEAVY_WORDS = 80  # pages with fewer extractable words than this are likely scanned/graphic


def clean_text(text: str) -> str:
    """Normalise brochure text so quotes and numbers can be matched reliably."""
    text = unicodedata.normalize("NFKC", text)          # ligatures like "ﬁ" -> "fi"
    text = re.sub(r"`\s?(?=\d)", "₹", text)              # Care's font renders ₹ as a backtick
    text = text.replace("\u00a0", " ").replace("\u00ad", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def open_pdf(code: str):
    path = config.POLICY_DIR / config.POLICIES[code]["file"]
    if not path.exists():
        raise FileNotFoundError(f"{path.name} is missing from data/policies/")
    try:
        return pymupdf.open(path)
    except Exception as e:
        raise ValueError(f"{path.name} could not be opened as a PDF: {e}") from e


def extract_pages(code: str) -> list[dict]:
    """Text and layout blocks for each page of a policy brochure."""
    pages = []
    with open_pdf(code) as doc:
        for index, page in enumerate(doc, start=1):
            blocks = [
                clean_text(b[4]) for b in page.get_text("blocks", sort=True)
                if b[6] == 0 and b[4].strip()  # b[6] == 0 means a text block
            ]
            text = "\n".join(blocks)
            words = len(text.split())
            pages.append({
                "policy": code,
                "page": index,
                "text": text,
                "blocks": blocks,
                "word_count": words,
                "image_heavy": words < IMAGE_HEAVY_WORDS,
            })
    return pages


def render_page_images(code: str, dpi: int = 110) -> list[bytes]:
    """PNG image of every page, sent to Gemini so it can read layout, tables and footnote markers."""
    with open_pdf(code) as doc:
        return [page.get_pixmap(dpi=dpi).tobytes("png") for page in doc]


def chunk_pages(pages: list[dict], target_words: int = CHUNK_TARGET_WORDS) -> list[dict]:
    """Merge layout blocks into ~target_words chunks. IDs look like HDFC-p11-c03."""
    chunks = []
    for page in pages:
        buffer, count, n = [], 0, 0

        def flush():
            nonlocal buffer, count, n
            if buffer:
                n += 1
                chunks.append({
                    "chunk_id": f"{page['policy']}-p{page['page']}-c{n:02d}",
                    "policy": page["policy"],
                    "page": page["page"],
                    "text": " ".join(buffer),
                })
            buffer, count = [], 0

        for block in page["blocks"]:
            for piece in _split_long(block, target_words):
                words = len(piece.split())
                if count and count + words > target_words * 1.5:
                    flush()
                buffer.append(piece)
                count += words
                if count >= target_words:
                    flush()
        flush()
    return chunks


def _split_long(block: str, target_words: int) -> list[str]:
    """Split a very long block into ~target_words pieces so no chunk is oversized."""
    words = block.split()
    if len(words) <= target_words * 1.5:
        return [" ".join(words)]
    return [" ".join(words[i:i + target_words]) for i in range(0, len(words), target_words)]


def ingest_all(codes: list[str] | None = None) -> dict:
    """Extract pages and chunks for the given policies and save them to data/processed/."""
    codes = codes or list(config.POLICIES)
    all_pages = _load_json(config.PAGES_FILE, default=[])
    all_chunks = _load_json(config.CHUNKS_FILE, default=[])

    # Replace only the policies being rebuilt
    all_pages = [p for p in all_pages if p["policy"] not in codes]
    all_chunks = [c for c in all_chunks if c["policy"] not in codes]

    report = {}
    for code in codes:
        pages = extract_pages(code)
        chunks = chunk_pages(pages)
        all_pages.extend({k: v for k, v in p.items() if k != "blocks"} for p in pages)
        all_chunks.extend(chunks)
        report[code] = {
            "pages": len(pages),
            "chunks": len(chunks),
            "image_heavy_pages": [p["page"] for p in pages if p["image_heavy"]],
        }

    _save_json(config.PAGES_FILE, all_pages)
    _save_json(config.CHUNKS_FILE, all_chunks)
    return report


def load_pages() -> list[dict]:
    return _load_json(config.PAGES_FILE, default=[])


def load_chunks() -> list[dict]:
    return _load_json(config.CHUNKS_FILE, default=[])


def _load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")