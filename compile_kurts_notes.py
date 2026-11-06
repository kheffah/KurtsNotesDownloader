#!/usr/bin/env python3
"""
Scrapes Kurt's Notes (https://schaberg.faculty.ucdavis.edu/), downloads all
individual pathology guide PDFs with smart caching, and compiles them into a
single PDF with a clickable Table of Contents and PDF sidebar bookmarks.
"""

import hashlib
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import fitz  # PyMuPDF
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://schaberg.faculty.ucdavis.edu/"
CACHE_DIR = Path(".kurts_notes_cache")
OUTPUT_PDF = Path("Kurts_Notes_Compiled.pdf")

# Exclude pre-compiled bundles or non-note PDFs if encountered
EXCLUDE_KEYWORDS = [
    "all of",
    "one file",
    "harding-forrester",
    "compiled",
    "schaberg_cv",
    "curriculum-vitae",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
}


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def scrape_note_links(base_url: str = BASE_URL) -> list[dict]:
    """
    Scrapes the homepage in document order, grouping PDF links under their
    organ-system section headings.
    """
    resp = requests.get(base_url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    content = (
        soup.select_one(".entry-content")
        or soup.select_one("main")
        or soup.select_one("article")
        or soup.body
    )

    notes = []
    current_category = "General"

    for el in content.find_all(["h1", "h2", "h3", "h4", "strong", "a"]):
        # Track organ-system section headers
        if el.name in ("h1", "h2", "h3", "h4"):
            heading_text = clean_text(el.get_text())
            if heading_text and len(heading_text) < 80:
                if not any(
                    skip in heading_text.lower()
                    for skip in ["diagnostic guides", "news and updates", "disclaimer"]
                ):
                    current_category = heading_text

        elif el.name == "strong" and not el.find("a") and not el.find_parent("a"):
            # Fallback if a section heading is styled as a standalone <strong> tag
            parent_text = clean_text(el.parent.get_text()) if el.parent else ""
            strong_text = clean_text(el.get_text())
            if strong_text and strong_text == parent_text and len(strong_text) < 80:
                current_category = strong_text

        elif el.name == "a" and el.get("href"):
            href = el["href"].strip()
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)

            if not parsed.path.lower().endswith(".pdf"):
                continue

            title = clean_text(el.get_text())
            if not title:
                title = Path(parsed.path).stem.replace("-", " ").replace("_", " ")

            combined_check = f"{title} {parsed.path}".lower()
            if any(kw in combined_check for kw in EXCLUDE_KEYWORDS):
                print(f"Skipping excluded file: {title} ({full_url})")
                continue

            notes.append(
                {
                    "category": current_category,
                    "title": title,
                    "url": full_url,
                }
            )

    return notes


def download_pdf(url: str, cache_dir: Path = CACHE_DIR) -> Path:
    """
    Downloads a PDF into cache_dir. Because updated notes on WordPress receive
    a new URL path (or filename), hashing the URL keeps cached files current
    while avoiding redundant downloads.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(urlparse(url).path).name)
    local_path = cache_dir / f"{url_hash}_{safe_name}"

    if local_path.exists() and local_path.stat().st_size > 0:
        return local_path

    print(f"Downloading: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    local_path.write_bytes(resp.content)
    return local_path


def build_compiled_pdf(notes: list[dict], output_path: Path = OUTPUT_PDF):
    """
    Merges all downloaded PDFs, constructs a clickable front-matter Table of
    Contents, and adds a nested PDF bookmark tree.
    """
    body_doc = fitz.open()
    url_to_page_start: dict[str, int] = {}
    toc_entries = []  # (category, title, body_start_page_0_idx)

    for item in notes:
        url = item["url"]
        category = item["category"]
        title = item["title"]

        # If a note is cross-listed under two sections, point the second TOC
        # entry to the first copy rather than duplicating pages in the PDF.
        if url in url_to_page_start:
            toc_entries.append((category, f"{title} (cross-listed)", url_to_page_start[url]))
            continue

        try:
            pdf_path = download_pdf(url)
            with fitz.open(pdf_path) as src_pdf:
                if src_pdf.page_count == 0:
                    continue
                start_page = body_doc.page_count
                body_doc.insert_pdf(src_pdf)
                url_to_page_start[url] = start_page
                toc_entries.append((category, title, start_page))
        except Exception as exc:
            print(f"Warning: Failed to process '{title}' ({url}): {exc}")

    # Calculate how many pages the front-matter TOC requires
    toc_lines = []
    last_cat = None
    for cat, title, start_page in toc_entries:
        if cat != last_cat:
            toc_lines.append(("CATEGORY", cat, None))
            last_cat = cat
        toc_lines.append(("ITEM", title, start_page))

    lines_per_first_page = 30
    lines_per_subsequent_page = 36
    if len(toc_lines) <= lines_per_first_page:
        toc_page_count = 1
    else:
        remaining = len(toc_lines) - lines_per_first_page
        toc_page_count = 1 + (remaining + lines_per_subsequent_page - 1) // lines_per_subsequent_page

    # Render TOC pages into final_doc
    final_doc = fitz.open()
    page_width, page_height = 612, 792  # US Letter (8.5 x 11 in)
    margin_x = 50
    pending_links = []  # (toc_page_idx, fitz.Rect, target_final_page_idx)

    line_idx = 0
    for page_i in range(toc_page_count):
        page = final_doc.new_page(width=page_width, height=page_height)
        y = 55

        if page_i == 0:
            page.insert_text(
                (margin_x, y),
                "Kurt's Notes — Compiled Diagnostic Guides",
                fontsize=20,
                fontname="helv",
                color=(0.07, 0.22, 0.42),
            )
            y += 22
            compiled_date = datetime.now().strftime("%B %d, %Y")
            page.insert_text(
                (margin_x, y),
                f"Source: {BASE_URL}  |  Compiled: {compiled_date}",
                fontsize=9.5,
                fontname="helv",
                color=(0.35, 0.35, 0.35),
            )
            y += 14
            page.draw_line(
                (margin_x, y),
                (page_width - margin_x, y),
                color=(0.07, 0.22, 0.42),
                width=1.2,
            )
            y += 24
            page.insert_text(
                (margin_x, y),
                "Table of Contents (Click any topic to jump to section)",
                fontsize=12,
                fontname="hebo",
                color=(0.15, 0.15, 0.15),
            )
            y += 22
            max_lines_this_page = lines_per_first_page
        else:
            page.insert_text(
                (margin_x, y),
                "Table of Contents (continued)",
                fontsize=12,
                fontname="hebo",
                color=(0.35, 0.35, 0.35),
            )
            y += 24
            max_lines_this_page = lines_per_subsequent_page

        lines_drawn = 0
        while line_idx < len(toc_lines) and lines_drawn < max_lines_this_page:
            kind, label, body_start = toc_lines[line_idx]
            if kind == "CATEGORY":
                y += 6
                page.insert_text(
                    (margin_x, y),
                    label,
                    fontsize=11,
                    fontname="hebo",
                    color=(0.07, 0.22, 0.42),
                )
                y += 16
            else:
                target_page_0 = toc_page_count + body_start
                display_page_num = str(target_page_0 + 1)

                # Truncate very long titles cleanly
                display_title = label if len(label) <= 68 else label[:65] + "..."
                item_x = margin_x + 14

                page.insert_text(
                    (item_x, y),
                    display_title,
                    fontsize=9.5,
                    fontname="helv",
                    color=(0.1, 0.1, 0.1),
                )

                # Right-aligned page number
                num_width = fitz.get_text_length(display_page_num, fontname="helv", fontsize=9.5)
                num_x = page_width - margin_x - num_width
                page.insert_text(
                    (num_x, y),
                    display_page_num,
                    fontsize=9.5,
                    fontname="helv",
                    color=(0.1, 0.1, 0.1),
                )

                # Dotted leader line between title and page number
                title_width = fitz.get_text_length(display_title, fontname="helv", fontsize=9.5)
                dots_start = item_x + title_width + 6
                dots_end = num_x - 6
                if dots_end > dots_start:
                    page.draw_line(
                        (dots_start, y - 2),
                        (dots_end, y - 2),
                        color=(0.75, 0.75, 0.75),
                        width=0.5,
                        dashes="[1 3] 0",
                    )

                link_rect = fitz.Rect(item_x, y - 10, page_width - margin_x, y + 3)
                pending_links.append((page_i, link_rect, target_page_0))
                y += 16

            line_idx += 1
            lines_drawn += 1

    # Append all note pages after the TOC pages
    final_doc.insert_pdf(body_doc)
    body_doc.close()

    # Activate clickable links on the TOC pages
    for toc_page_idx, rect, target_page_0 in pending_links:
        final_doc[toc_page_idx].insert_link(
            {
                "kind": fitz.LINK_GOTO,
                "from": rect,
                "page": target_page_0,
            }
        )

    # Build the PDF Sidebar Outline (Bookmarks)
    pdf_outline = [[1, "Table of Contents", 1]]
    current_cat = None
    for cat, title, body_start in toc_entries:
        target_page_1based = toc_page_count + body_start + 1
        if cat != current_cat:
            pdf_outline.append([1, cat, target_page_1based])
            current_cat = cat
        pdf_outline.append([2, title, target_page_1based])

    final_doc.set_toc(pdf_outline)
    final_doc.save(output_path, garbage=4, deflate=True)
    print(f"\nSuccess! Saved {final_doc.page_count}-page compiled book to: {output_path.resolve()}")
    final_doc.close()


if __name__ == "__main__":
    print(f"Fetching index from {BASE_URL} ...")
    scraped_notes = scrape_note_links()
    print(f"Found {len(scraped_notes)} note links across categories.")
    build_compiled_pdf(scraped_notes)
