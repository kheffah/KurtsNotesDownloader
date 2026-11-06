# Kurt's Notes — Automated PDF Compiler

[![Build Kurt's Notes Compiled PDF](../../actions/workflows/update-pdf.yml/badge.svg)](../../actions/workflows/update-pdf.yml)
[![Download Latest PDF](https://img.shields.io/badge/Download_Latest_PDF-Kurt's_Notes_Compiled-0b386b?style=for-the-badge&logo=adobeacrobatreader&logoColor=white)](../../releases/latest/download/Kurts_Notes_Compiled.pdf)

An automated Python tool and GitHub Actions workflow that scrapes the individual surgical and medical pathology guides from **[Kurt's Notes](https://schaberg.faculty.ucdavis.edu/)** (authored by Dr. Kurt Schaberg at UC Davis) and compiles them into a single, navigable PDF book.

---

##📥 Download the Latest Compiled Book

Click the link below to download the most recent automatically compiled edition:

👉 **[Download `Kurts_Notes_Compiled.pdf` (Latest Release)](../../releases/latest/download/Kurts_Notes_Compiled.pdf)**

*(Note: Because the link above uses GitHub's relative `releases/latest/download` redirect, it always serves the newest build.)*

---

## ✨ Features of the Compiled PDF

* **Clickable Front-Matter Table of Contents:** Automatically organized by the organ-system categories on Dr. Schaberg's homepage, with dotted leader lines and page numbers.
* **Hierarchical PDF Bookmarks:** Includes a collapsible sidebar outline grouped by section and topic for fast navigation in any PDF viewer.
* **Interactive Running Footers:** Every body page is stamped with a clean footer displaying:
  * **Left:** Organ-system category and guide title
  * **Center:** A clickable **`[ Back to TOC ]`** link that jumps straight back to Page 1
  * **Right:** Global page numbering (`Page X of Y`)
* **Smart Cross-List Deduplication:** Guides listed under multiple organ systems on the homepage appear in every relevant section of the Table of Contents and bookmarks without duplicating pages in the PDF file.
* **Intelligent Caching:** Caches downloaded PDFs by URL hash so subsequent runs only download newly added or updated guides.

---

## 🔄 How Updates Work (GitHub Actions)

This repository uses GitHub Actions (`.github/workflows/update-pdf.yml`) to keep the compiled book up to date without manual intervention:

1. **Automatic Weekly Schedule:** Every Monday at `08:00 UTC`, GitHub Actions runs `compile_kurts_notes.py`, builds a fresh PDF, and publishes it to the **Latest Release**.
2. **One-Click Manual Trigger:** Whenever a new guide is posted and you want an immediate rebuild:
   1. Open the **[Actions](../../actions/workflows/update-pdf.yml)** tab at the top of this repository.
   2. Select **Build Kurt's Notes Compiled PDF** in the left sidebar.
   3. Click the **Run workflow** dropdown button on the right and press **Run workflow**.
   4. Within ~60 seconds, the updated PDF will be published to the latest release link.

### Permanent Link for External Websites
If you want to link directly to the auto-updating PDF from an external website (such as a WordPress homepage), use the full permanent release URL:

```text
https://github.com/<YOUR-USERNAME>/<YOUR-REPO-NAME>/releases/latest/download/Kurts_Notes_Compiled.pdf
```

---

## 💻 Running the Script Locally

If you prefer to build the PDF on your own computer:

### 1. Prerequisites
Make sure you have **Python 3.9+** installed.

### 2. Install Dependencies
```bash
pip install requests beautifulsoup4 pymupdf
```

### 3. Run the Compiler
```bash
python compile_kurts_notes.py
```

The script will cache individual notes inside `.kurts_notes_cache/` and output **`Kurts_Notes_Compiled.pdf`** in the current directory.

---

## 📁 Repository Structure

```text
.
├── .github/
│   └── workflows/
│       └── update-pdf.yml      # Automated weekly & one-click GitHub Actions build
├── compile_kurts_notes.py      # Scraper and PDF compiler script
└── README.md                   # Repository documentation
```

---

## 📜 Attribution & Disclaimer

All pathology diagnostic guides compiled by this script are the intellectual property and work of **Dr. Kurt Schaberg, MD** ([https://schaberg.faculty.ucdavis.edu/](https://schaberg.faculty.ucdavis.edu/)). This repository only provides the open-source automation code to merge those publicly available guides for convenient offline reading.
