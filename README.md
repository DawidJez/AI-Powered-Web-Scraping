# AI-Powered Web Scraping Pipeline

An AI-driven scraping tool for electronics component catalog pages (Texas Instruments,
Vishay, Analog Devices, Infineon...). Given a URL, a natural-language description of what
to extract, and a filter value/list, it navigates the page's filter UI, paginates through
results, and exports structured product data to Excel (with optional PDF datasheet
downloads) — without hardcoded selectors for the general case.

## How it works

The pipeline orchestrates two AI-driven layers (Gemini) against a browser-automation
layer (Firecrawl):

1. **Probe** (`page_sample`) - takes a first look at the page and infers its structure:
   available fields, whether pagination/filtering exists, filter cardinality per field
   (`identifier` vs `category`), and a human-readable summary.
2. **Filter resolution** (`search_for_filters` / `resolve_selectors`) - two AI prompts
   find the actual JS needed to open and interact with the filter UI for a given field
   (gate script + target script, or a list of clickable options for category-type
   filters). Handles AG Grid quirks, Shadow DOM web components, and React-controlled
   inputs that ignore plain `.value` assignment.
3. **Pagination resolution** (`resolve_pagination`) - a dedicated, shorter AI prompt
   finds the "next page" control and (when visible) a disabled-state check, cached to
   disk per URL for 24h.
4. **Filtered scraping** (`fetch_filtered_data`) - applies the resolved filter script(s)
   for each value in the input list and scrapes the resulting page.
5. **Extraction** (`extract`) - a third AI call reads the scraped Markdown/HTML and pulls
   out the fields the user asked for, one record per match.
6. **Pagination loop** (`fetch_with_pagination` / `extract_data`) - if a page's record
   count matches its declared total, the pipeline re-applies the filter and paginates
   (multiple `scrape` actions inside a single Firecrawl session) until all records are
   collected.
7. **Export** - flattened records go to `.xlsx` via `openpyxl`; optionally, PDF
   datasheets are downloaded per record.

## Requirements

```
firecrawl-py
beautifulsoup4
google-genai
openpyxl
markdownify
requests
```

Install with:
```bash
pip install -r requirements.txt --break-system-packages
```

### Environment / accounts
- A Firecrawl API key (browser automation + scraping).
- A Google Gemini API key. Working free-tier models on this account:
  `gemini-flash-latest`, `gemini-3.5-flash`, `gemini-3.1-flash-lite`.
  Newer aliases (e.g. `gemini-3.6-flash`) may silently swap in with a different,
  stricter free-tier rate limit - pin an explicit model string rather than relying on
  `-latest` if you hit unexpected 429s.

## Usage

The whole program lives inside `orchestrator.run()` - it's fully interactive and walks
the user through every step via prompts in the terminal. No parameters to fill in code.

```python
orchestrator = Orchestrator(...)
orchestrator.run()
```

Running it looks like this:

1. **URL** - paste the catalog page URL.
2. **Page preview** - a readable summary of what was found on the page (available
   fields, filterable fields) is printed for a sanity check.
3. **Values to search for** - choose to either type them directly (comma-separated),
   or pick a file from disk via a file picker window (`.csv`/`.txt`/`.xlsx`/`.xls`).
   A plain CSV/TXT file should just be a flat, comma-separated list:
   `item1, item2, item3, ...`
4. **Filter field** - the name of the field to filter by (e.g. `Product number`),
   plus up to two optional hint words if the field name is ambiguous on the page.
5. **What to extract** - a free-text description of which fields to pull per record
   (e.g. "numer, cena, obudowa, temperatura pracy").
6. **Output path** - where to save the `.xlsx` (defaults to `data/excel/output.xlsx`
   if left blank).
7. **PDF datasheets** - optionally download each record's PDF datasheet too (off by
   default - it's slow).

The result is also returned as a dict (`output_path`, `record_count`, `records`) for
anyone calling `run()` from other code rather than a terminal.

### Filter field + hints
The field name should match what's visible on the page's filter UI (column header,
filter panel label, etc.). The two optional hint words help the AI disambiguate when
a field name is reused or unclear - e.g. `Pin count` + hints `OR` / `102` when the page
has both a filter mode toggle and example values nearby.

### Values file format
Flat list, comma/semicolon/newline separated - e.g. a `.csv`/`.txt` with
`TMP75, TMP117, ISOTMP35`. Excel files are also supported: every non-empty cell across
all rows/columns is read as one value.

One fetch is performed per value - for `identifier` cardinality fields (e.g. a part
number search box) each value is typed in; for `category` fields (e.g.
checkboxes/dropdowns) each value is fuzzy-matched against the available options.

## Known limitations / in-progress work

- **Range slider filters** are not yet supported (identified gap, not implemented).
- **Site-specific patches**: `analog.com`'s parametric search grid is virtualized and
  doesn't expose pagination controls or page counts in the static HTML/Markdown, so
  both the next-button script and the total-record-count detection use dedicated,
  hardcoded logic for that domain rather than the general AI-driven resolver.
- **Generalization beyond TI** is intentionally incremental - Vishay and Analog Devices
  work but required their own debugging passes (React-controlled selects, virtualized
  grids); Infineon is untested as of this writing.
- AI selector resolution is **not fully deterministic** - the same prompt can
  occasionally pick an invalid element (e.g. a disabled input) on one run and the
  correct one on the next. Defensive checks exist in the orchestrator
  (`known_disabled_pattern`) for known problem cases, but this class of bug can
  resurface on new pages/fields.

## Architecture notes for contributors

- **Never trust the model's response shape blindly.** Every AI JSON call is validated
  (`isinstance(parsed, dict)` + expected keys) inside its retry loop - a malformed
  response is treated the same as a parse failure and retried, not silently degraded.
- **Prompts are kept single-purpose.** Cramming multiple tasks into one AI call (e.g.
  "extract records AND count pages AND count total items") reliably degrades the
  primary task's completeness. Secondary info (page counts, totals) is extracted via
  separate, cheap, dedicated calls when it can't be derived deterministically.
- **IIFE-wrap every generated JS script.** Multiple `executeJavascript` actions share
  one page-level scope in a single Firecrawl session; unwrapped `const`/`let` across
  scripts throws `Identifier already declared`.
- **Scope selectors through unique context**, never through repeated CSS classes alone
  (AG Grid renders the same filter classes once per column) - match via header text or
  a shared positional attribute (e.g. `aria-colindex`) instead.
- **React-controlled inputs ignore plain `.value =` assignment.** Single dropdowns need
  a native-setter workaround; multi-select listboxes need a native-setter "prime" step
  followed by a synthetic click on the target `<option>`.
- Gemini's free-tier rate limit is **per-minute input tokens**, not a simple daily
  total - bursts of large-HTML prompts in quick succession can trip it well before any
  dashboard total looks concerning. Handle `errors.ClientError` (429) with a real
  retry/backoff, not just `errors.ServerError`.