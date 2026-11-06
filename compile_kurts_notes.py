#!/usr/bin/env python3
"""
Scrapes Kurt's Notes (https://schaberg.faculty.ucdavis.edu/), downloads all
individual pathology guide PDFs using Chrome TLS impersonation (curl_cffi),
prunes outdated cached files, determines the most recent Last-Modified date
across all notes, and compiles them into Kurts_Notes_Compiled_YYYY-MM-DD.pdf
with:
  1. A clickable front-matter Table of Contents
  2. Hierarchical PDF sidebar bookmarks
  3. A subtle footer on every body page with the section/title, clickable
     "Back to TOC" link, and global page number ("Page X of Y")
"""

import hashlib
import os
import re
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import fitz  # PyMuPDF
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

BASE_URL = "https://schaberg.faculty.ucdavis.edu/"
CACHE_DIR = Path(".kurts_notes_cache")
FILENAME_RECORD = Path("latest_filename.txt")
MAX_LOCAL_EDITIONS = 2

# Exclude pre-compiled bundles or non-note PDFs if encountered
EXCLUDE_KEYWORDS = [
    "all of",
    "one file",
    "harding-forrester",
    "compiled",
    "schaberg_cv",
    "curriculum-vitae",
]

BROWSER_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def create_browser_session() -> cffi_requests.Session:
    """
    Creates a session that impersonates Chrome's TLS/JA3 and HTTP/2 fingerprint
    so UC Davis / CampusPress WAF does not return 403 Forbidden.
    """
    session = cffi_requests.Session(impersonate="chrome")
    session.headers.update(BROWSER_HEADERS)
    return session


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def truncate_to_width(
    text: str, max_width: float, fontname: str = "helv", fontsize: float = 8.0
) -> str:
    """Truncates a string with '...' so its rendered width stays under max_width."""
    if fitz.get_text_length(text, fontname=fontname, fontsize=fontsize) <= max_width:
        return text
    ellipsis = "..."
    avail = max_width - fitz.get_text_length(ellipsis, fontname=fontname, fontsize=fontsize)
    trimmed = text
    while trimmed and fitz.get_text_length(trimmed, fontname=fontname, fontsize=fontsize) > avail:
        trimmed = trimmed[:-1]
    return trimmed.rstrip() + ellipsis


def fetch_homepage_html(session: cffi_requests.Session, base_url: str = BASE_URL) -> str:
    """
    Fetches the homepage HTML using Chrome TLS impersonation, falling back to
    the WordPress REST API (/wp-json/wp/v2/pages) if the main route returns 403.
    """
    resp = session.get(base_url, timeout=30)
    if resp.status_code == 200 and "<a " in resp.text:
        return resp.text

    print(f"Direct homepage fetch returned HTTP {resp.status_code}; trying WordPress REST API...")
    api_url = urljoin(base_url, "wp-json/wp/v2/pages?per_page=20")
    api_resp = session.get(
        api_url,
        headers={"Accept": "application/json", "Referer": base_url},
        timeout=30,
    )
    api_resp.raise_for_status()
    pages_json = api_resp.json()
    combined_html = "\n".join(
        p.get("content", {}).get("rendered", "") for p in pages_json if isinstance(p, dict)
    )
    if not combined_html.strip():
        resp.raise_for_status()
    return f"<html><body><main>{combined_html}</main></body></html>"


def scrape_note_links(session: cffi_requests.Session, base_url: str = BASE_URL) -> list[dict]:
    """
    Scrapes the homepage in document order, grouping PDF links under their
    organ-system section headings.
    """
    html = fetch_homepage_html(session, base_url)
    soup = BeautifulSoup(html, "html.parser")

    content = (
        soup.select_one(".entry-content")
        or soup.select_one("main")
        or soup.select_one("article")
        or soup.body
    )

    notes = []
    current_category = "General"

    for el in content.find_all(["h1", "h2", "h3", "h4", "strong", "a"]):
        if el.name in ("h1", "h2", "h3", "h4"):
            heading_text = clean_text(el.get_text())
            if heading_text and len(heading_text) < 80:
                if not any(
                    skip in heading_text.lower()
                    for skip in ["diagnostic guides", "news and updates", "disclaimer"]
                ):
                    current_category = heading_text

        elif el.name == "strong" and not el.find("a") and not el.find_parent("a"):
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


def download_pdf(
    session: cffi_requests.Session, url: str, cache_dir: Path = CACHE_DIR
) -> Path:
    """
    Downloads a PDF into cache_dir and preserves the server's Last-Modified
    timestamp on the cached file so we can determine the latest update date.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(urlparse(url).path).name)
    local_path = cache_dir / f"{url_hash}_{safe_name}"

    if local_path.exists() and local_path.stat().st_size > 0:
        return local_path

    print(f"Downloading: {url}")
    pdf_headers = {
        "Accept": "application/pdf,*/*;q=0.8",
        "Referer": BASE_URL,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    }
    resp = session.get(url, headers=pdf_headers, timeout=60)
    resp.raise_for_status()
    local_path.write_bytes(resp.content)

    # Preserve the server's Last-Modified date on the cached file
    last_mod = resp.headers.get("Last-Modified")
    if last_mod:
        try:
            dt = parsedate_to_datetime(last_mod)
            ts = dt.timestamp()
            os.utime(local_path, (ts, ts))
        except Exception:
            pass

    # Brief pause between fresh downloads to avoid triggering WAF rate limits
    time.sleep(0.3)
    return local_path


def prune_cache_and_old_builds(used_cache_files: set[Path], max_editions: int = MAX_LOCAL_EDITIONS):
    """
    1. Deletes any cached single-chapter PDFs that are no longer linked on Kurt's site.
    2. Keeps only the `max_editions` newest compiled PDFs in the working directory.
    """
    if used_cache_files and CACHE_DIR.exists():
        for cached_file in CACHE_DIR.glob("*.pdf"):
            if cached_file.resolve() not in used_cache_files:
                print(f"Pruning outdated cache file: {cached_file.name}")
                cached_file.unlink(missing_ok=True)

    compiled_files = sorted(Path(".").glob("Kurts_Notes_Compiled_*.pdf"), reverse=True)
    for old_pdf in compiled_files[max_editions:]:
        print(f"Removing older local compiled edition: {old_pdf.name}")
        old_pdf.unlink(missing_ok=True)


def add_body_footers(
    final_doc: fitz.Document, toc_page_count: int, body_page_meta: list[tuple[str, str]]
):
    """
    Stamps a subtle footer on every body page with:
      - Left:   Section Name - Note Title
      - Center: Clickable '[ Back to TOC ]' link jumping to page 1
      - Right:  Global page number ('Page X of Y')
    """
    total_pages = final_doc.page_count
    font_size = 8.0
    margin_x = 36.0
    back_label = "[ Back to TOC ]"
    back_width = fitz.get_text_length(back_label, fontname="hebo", fontsize=font_size)

    for body_idx, (category, title) in enumerate(body_page_meta):
        page_idx = toc_page_count + body_idx
        page = final_doc[page_idx]

        # Isolate existing page graphics state so footer coordinates are never
        # distorted by transformation matrices inside the source PDF
        page.wrap_contents()

        rect = page.rect
        footer_top = rect.y1 - 20.0
        baseline_y = rect.y1 - 7.5
        center_x = (rect.x0 + rect.x1) / 2.0

        bg_rect = fitz.Rect(rect.x0, footer_top, rect.x1, rect.y1)
        page.draw_rect(bg_rect, color=None, fill=(1.0, 1.0, 1.0))
        page.draw_line(
            (rect.x0 + margin_x, footer_top),
            (rect.x1 - margin_x, footer_top),
            color=(0.82, 0.82, 0.82),
            width=0.4,
        )

        # 1. Left: Section name & note title
        left_x = rect.x0 + margin_x
        back_start_x = center_x - (back_width / 2.0)
        max_left_width = max(60.0, back_start_x - left_x - 14.0)
        raw_section_label = f"{category} - {title}"
        section_label = truncate_to_width(
            raw_section_label, max_left_width, fontname="helv", fontsize=font_size
        )

        page.insert_text(
            (left_x, baseline_y),
            section_label,
            fontsize=font_size,
            fontname="helv",
            color=(0.40, 0.40, 0.40),
        )

        # 2. Center: Clickable '[ Back to TOC ]' link
        page.insert_text(
            (back_start_x, baseline_y),
            back_label,
            fontsize=font_size,
            fontname="hebo",
            color=(0.07, 0.22, 0.42),
        )
        link_rect = fitz.Rect(
            back_start_x - 4.0,
            footer_top + 1.0,
            back_start_x + back_width + 4.0,
            rect.y1 - 2.0,
        )
        page.insert_link(
            {
                "kind": fitz.LINK_GOTO,
                "from": link_rect,
                "page": 0,
            }
        )

        # 3. Right: Global page number
        page_num_str = f"Page {page_idx + 1} of {total_pages}"
        page_num_width = fitz.get_text_length(page_num_str, fontname="helv", fontsize=font_size)
        right_x = rect.x1 - margin_x - page_num_width

        page.insert_text(
            (right_x, baseline_y),
            page_num_str,
            fontsize=font_size,
            fontname="helv",
            color=(0.40, 0.40, 0.40),
        )


def build_compiled_pdf(session: cffi_requests.Session, notes: list[dict]) -> Path:
    """
    Merges all downloaded PDFs, constructs a clickable front-matter Table of
    Contents, stamps footers on every body page, builds the PDF bookmark tree,
    and prunes obsolete cache files.
    """
    body_doc = fitz.open()
    url_to_page_start: dict[str, int] = {}
    toc_entries = []
    body_page_meta: list[tuple[str, str]] = []
    used_cache_files: set[Path] = set()
    latest_mtime = 0.0

    for item in notes:
        url = item["url"]
        category = item["category"]
        title = item["title"]

        if url in url_to_page_start:
            toc_entries.append((category, f"{title} (cross-listed)", url_to_page_start[url]))
            continue

        try:
            pdf_path = download_pdf(session, url)
            used_cache_files.add(pdf_path.resolve())
            mtime = pdf_path.stat().st_mtime
            if mtime > latest_mtime:
                latest_mtime = mtime

            with fitz.open(pdf_path) as src_pdf:
                num_pages = src_pdf.page_count
                if num_pages == 0:
                    continue
                start_page = body_doc.page_count
                body_doc.insert_pdf(src_pdf)
                url_to_page_start[url] = start_page
                toc_entries.append((category, title, start_page))
                for _ in range(num_pages):
                    body_page_meta.append((category, title))
        except Exception as exc:
            print(f"Warning: Failed to process '{title}' ({url}): {exc}")

    if latest_mtime > 0:
        last_update_dt = datetime.fromtimestamp(latest_mtime)
    else:
        last_update_dt = datetime.now()

    date_slug = last_update_dt.strftime("%Y-%m-%d")
    human_date = last_update_dt.strftime("%B %d, %Y")
    output_path = Path(f"Kurts_Notes_Compiled_{date_slug}.pdf")

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
        toc_page_count = (
            1 + (remaining + lines_per_subsequent_page - 1) // lines_per_subsequent_page
        )

    final_doc = fitz.open()
    page_width, page_height = 612, 792
    margin_x = 50
    pending_links = []

    line_idx = 0
    for page_i in range(toc_page_count):
        page = final_doc.new_page(width=page_width, height=page_height)
        y = 55

        if page_i == 0:
            page.insert_text(
                (margin_x, y),
                "Kurt's Notes - Compiled Diagnostic Guides",
                fontsize=20,
                fontname="helv",
                color=(0.07, 0.22, 0.42),
            )
            y += 22
            page.insert_text(
                (margin_x, y),
                f"Source: {BASE_URL}  |  Last Note Update: {human_date}",
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

                display_title = label if len(label) <= 68 else label[:65] + "..."
                item_x = margin_x + 14

                page.insert_text(
                    (item_x, y),
                    display_title,
                    fontsize=9.5,
                    fontname="helv",
                    color=(0.1, 0.1, 0.1),
                )

                num_width = fitz.get_text_length(display_page_num, fontname="helv", fontsize=9.5)
                num_x = page_width - margin_x - num_width
                page.insert_text(
                    (num_x, y),
                    display_page_num,
                    fontsize=9.5,
                    fontname="helv",
                    color=(0.1, 0.1, 0.1),
                )

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

    final_doc.insert_pdf(body_doc)
    body_doc.close()

    for toc_page_idx, rect, target_page_0 in pending_links:
        final_doc[toc_page_idx].insert_link(
            {
                "kind": fitz.LINK_GOTO,
                "from": rect,
                "page": target_page_0,
            }
        )

    add_body_footers(final_doc, toc_page_count, body_page_meta)

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
    FILENAME_RECORD.write_text(output_path.name, encoding="utf-8")
    print(
        f"\nSuccess! Saved {final_doc.page_count}-page compiled book to: {output_path.resolve()}"
    )
    final_doc.close()

    # Prune replaced single-chapter cache files and keep at most 2 local compiled PDFs
    prune_cache_and_old_builds(used_cache_files)

    return output_path


if __name__ == "__main__":
    print(f"Fetching index from {BASE_URL} ...")
    browser_session = create_browser_session()
    scraped_notes = scrape_note_links(browser_session)
    print(f"Found {len(scraped_notes)} note links across categories.")
    build_compiled_pdf(browser_session, scraped_notes)
