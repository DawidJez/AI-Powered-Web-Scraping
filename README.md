# AI-Powered Web Scraping Pipeline

This repository is a prototype for scraping dynamic product catalog pages using a mix of:

- browser automation with Firecrawl
- AI-based page understanding with Gemini
- JavaScript-driven filter and pagination handling
- Excel export and optional PDF datasheet download

It is designed for catalog-like websites where the product list is rendered dynamically and the filter UI is not simple static HTML. Typical examples are electronics distributor pages and parametric search grids.

Important: this project is still an experimental automation pipeline, not a polished production-ready product. Some changes are needed before general use.

---

## Overview

### What this project solves

This system automates data extraction from complex, JavaScript-heavy product catalog websites where:
- Product lists are rendered dynamically
- Filters use custom UI (dropdowns, search boxes, checkboxes, AG Grid, React controls)
- Navigation requires JavaScript interaction
- Static HTML scrapers fail

### Typical use cases

- Electronics distributor sites (Texas Instruments, Infineon, Analog Devices)
- Parametric search grids with many filter types
- Sites with pagination or lazy-loading
- Product comparison pages
- DataSheet collection and export

### What it does

The system takes a website URL, a list of product identifiers (part numbers), and a data extraction prompt, then:

1. Analyzes the page structure with AI
2. Locates and understands the filter UI
3. Generates JavaScript to interact with filters
4. Filters products one by one
5. Extracts structured records with AI
6. Handles pagination
7. Exports results to Excel
8. Optionally downloads datasheets (PDFs)

---

## Quick Start

### Prerequisites

- **Python 3.8+** (3.9+ recommended, 3.11+ preferred)
- Firecrawl API key: https://www.firecrawl.dev/
- Google Gemini API key: https://aistudio.google.com/apikey

### Setup (5 minutes)

```bash
# 1. Clone and enter directory
cd ai-powered-web-scraping

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure credentials
cp .env.example .env
# Edit .env and add your API keys:
#   FIRECRAWL_API_KEY=your_key_here
#   GEMINI_API_KEY=your_key_here

# 5. Run
python src/main.py
```

### Test with example sites

Interactive test suite with pre-configured sites:

```bash
python src/test_orchestrator.py
```

Includes:
- Texas Instruments (sensors)
- Analog Devices (parametric search)
- Vishay (LEDs)
- Infineon (isolators)
- KOA Speer (resistors)
- And more...

---

## Architecture

### How the application works

The flow is orchestrated by `Orchestrator`. Here's what happens:

#### 1. Page reconnaissance

Fetch the page and ask AI: What fields exist? Which are filterable? Does pagination exist?

```
page_sample() → AI analysis → field detection
```

#### 2. Filter discovery

Find the correct filter element for the user's chosen field.

**Challenge:** Filters vary widely (custom boxes, dropdowns, checkboxes, AG Grid, React inputs, hidden elements).

#### 3. Script resolution

Generate JavaScript that:
- Opens the filter
- Types a value
- Selects an option
- Triggers page reload
- Waits for data

```
resolve_scripts() → generated JS snippets → tested against DOM
```

**Fragility point:** JS must work against actual target page structure.

#### 4. Filtered data fetch

Apply resolved logic to each product in the user's list.

```
filtered_scraping() → one value at a time → capture result
```

#### 5. Record extraction

Send page markdown to Gemini and ask for structured data extraction.

```
extract_data() → AI parsing → structured JSON records
```

#### 6. Pagination

If more pages exist, loop until all results collected.

**Weakness:** Many sites implement pagination uniquely.

#### 7. Export

Save to Excel via `ExcelExporter`. Optionally download PDFs with `PDFDownloader`.

### Key modules

| Module | Purpose |
|--------|---------|
| `orchestrator.py` | Main orchestration loop; coordinates all steps |
| `firecrawl_client.py` | Browser automation; page fetching and JS execution |
| `ai_extractor.py` | Gemini prompts; page analysis, filter detection, data extraction |
| `excel_exporter.py` | Generate Excel files from extracted records |
| `pdf_downloader.py` | Download datasheets for matched products |

### Typical flow in the interactive app

When the app is used interactively, the expected sequence is:

1. Paste a catalog URL
2. Review the page summary (AI-generated)
3. Provide product values (part numbers) manually or from CSV/TXT/XLSX file
4. Choose the field to filter by (e.g., `Product number`)
5. Optionally add hints for ambiguous field names (e.g., `part number`, `filter by product`)
6. Describe which product attributes to extract (custom prompt)
7. Choose an output file path
8. Decide whether to download datasheets

**Example:**

Product list:
```text
TMP75, TMP117, ISOTMP35
```

Filter configuration:
- Field: `Product number`
- Hint 1: `part number`
- Hint 2: `filter by product`

The app then locates the filter, generates interaction JavaScript, applies filters one by one, extracts records, and exports to Excel.

---

## Developers: Contributing to this project

### How to navigate this repo

**If you're new, read in this order:**

1. **`src/orchestrator.py`** — Main loop and overall flow
2. **`src/firecrawl_client.py`** — Browser automation and page fetching
3. **`src/ai_extractor.py`** — AI prompts and parsing logic
4. **`src/excel_exporter.py`** — Excel output
5. **`src/pdf_downloader.py`** — PDF downloads

### Directory layout

```
src/                      # Main application code
  orchestrator.py         # Orchestration layer
  firecrawl_client.py     # Browser automation
  ai_extractor.py         # AI-based understanding
  excel_exporter.py       # Excel export
  pdf_downloader.py       # PDF downloads
  main.py                 # Entry point
  test_orchestrator.py    # Interactive test suite

tests/
  manual_tests/           # Development test runners
  older_code/             # Legacy site-specific scrapers (reference only)

data/                     # Generated during runtime (ignored by git) 
  ├── datasheets/  # Cached PDF datasheets
  └── excel/              # Output Excel files

.env.example              # Template for credentials
requirements.txt          # Pinned dependency versions
```

### Note on data/ directory

- **Ignored in `.gitignore`** — Not included in repository
- **Auto-created** — Directory created when app first runs
- **Contains:** Debug dumps, cached results, test files, generated Excel files
- **Not shipped** — Cloned projects won't have this directory initially

### Note on tests/older_code/

Legacy code from earlier project iterations:
- Original `ProductFinder` class (Texas Instruments specific)
- Site-specific scraper logic
- **Why kept:** Reference material for project evolution
- **Why not used:** Logic now generalized via `Orchestrator` and AI extraction
- **Not imported:** These files are not part of the current pipeline

---

## Developers: Contributing to this project

### Before you change anything, understand these principles

#### 1. AI output must be validated

Never trust AI output blindly. Always validate extracted data and selector choices.

#### 2. Generated JavaScript must be scoped

JS snippets must be carefully scoped to avoid DOM collisions and side effects.

#### 3. Avoid generic CSS selectors

Don't rely only on repeated CSS classes (e.g., `.product-row`). Combine with unique attributes, text content, or structure.

#### 4. React inputs need workarounds

React-controlled inputs often reject plain value assignment. They need event simulation.

#### 5. Site structure varies widely

Pagination, filter UI, and record layout differ per site. Some site-specific logic is unavoidable.

#### 6. Handle API limits and flakes

Firecrawl and Gemini APIs have rate limits. Browser behavior can be flaky. Use retries, backoff, and timeouts.

### Performance expectations

- Page analysis: **50–60 seconds**
- Per-record filtering: **15–55 seconds** (depends on DOM complexity)
- Record extraction: **20–70 seconds** (depends on count and complexity, for each record with multiple response records or pages you should expect + 30s minutes per record)

---

## Best way to debug this repo

When a page fails, the most effective debugging flow is:

1. **Save intermediate files** — Check `data/` directory for HTML/markdown dumps. Inspect them or view elements in browser.
2. **Verify filter presence** — Confirm the filter element exists and is visible on the page.
3. **Inspect orchestrator step** — Find which step failed (page reconnaissance, filter discovery, script resolution, etc.).
4. **Test generated JavaScript** — Verify if the generated JS snippet is valid in the browser console.
5. **Validate AI output** — Check the structure of extracted data before trusting it.

**Remember:** Many failures are not Python exceptions. They're DOM issues caused by dynamic websites, hidden elements, or JavaScript state problems.

---

## Known Limitations

### 1. Filtering is not fully general

✗ Works on many sites, but not all catalog grids  
✗ Some logic remains site-specific  
✓ Covers most common filter types (dropdowns, search, checkboxes)

### 2. AI selectors aren't deterministic

The model may occasionally pick a stale or incorrect selector. This is a top quality issue.

### 3. Pagination is unreliable on some sites

✗ Virtualized grids, hidden counters, custom navigation still need debugging  
✗ No universal pagination pattern exists

### 4. Specialized filters missing

✗ Range sliders  
✗ Date pickers  
✗ Complex multi-select patterns

### 5. Code is intentionally experimental

- Prototype scripts still present
- Debug output scattered around
- Old helper logic not fully cleaned up

### 6. PDF download depends on site

Not all sites provide datasheet URLs. Extraction accuracy varies.

---

## Dependencies and versions

| Package | Version | Purpose |
|---------|---------|----------|
| firecrawl-py | 4.32.0 | Browser automation and web scraping |
| beautifulsoup4 | 4.15.0 | HTML parsing and element selection |
| google-genai | 2.11.0 | Google Gemini API client |
| openpyxl | 3.1.5 | Excel file generation and export |
| markdownify | 1.2.3 | Convert HTML to Markdown |
| requests | 2.34.2 | HTTP requests and API calls |
| python-dotenv | 1.2.2 | Load environment variables from `.env` |

All versions are pinned for reproducible builds. To update:

```bash
pip list --outdated
```

---

## Roadmap: What still needs to be developed

### High priority

1. **Generalize filter logic**
   - Support more filter types
   - Reduce site-specific patches
   - Improve selector reliability

2. **Improve pagination**
   - Detect page counts reliably
   - Support virtualized grids
   - Handle more next-page patterns

3. **Harden the AI contract**
   - Stricter JSON validation
   - Better error handling
   - Clearer retry logic

4. **Better UX**
   - Clearer error messages
   - Robust filter-not-found handling
   - Easier handling of ambiguous field names

5. **Add test coverage**
   - Unit tests for helpers
   - Fixture-based tests with sample HTML
   - Regression tests for known patterns

6. **Reduce hardcoded behavior**
   - Move repeated fixes into reusable abstractions
