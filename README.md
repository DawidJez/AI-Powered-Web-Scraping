# AI-Powered Web Scraping Pipeline

This repository is a prototype for scraping dynamic product catalog pages using a mix of:

- browser automation with Firecrawl
- AI-based page understanding with Gemini
- JavaScript-driven filter and pagination handling
- Excel export and optional PDF datasheet download

It is designed for catalog-like websites where the product list is rendered dynamically and the filter UI is not simple static HTML. Typical examples are electronics distributor pages and parametric search grids.

Important: this project is still an experimental automation pipeline, not a polished production-ready product. Some changes are needed before general use.

---

## What this project is trying to do

The system takes a website URL, a list of search values, and a prompt describing what to extract, then tries to:

1. inspect the page structure
2. find filterable fields
3. resolve the JavaScript needed to interact with those filters
4. apply the search values
5. scrape the filtered results
6. extract structured records with AI
7. handle pagination if required
8. save the output to Excel
9. optionally download datasheet PDFs

This is useful when a website exposes product data through grid interfaces, custom filters, and JavaScript-rendered components.

---

## How to navigate this repo

If you are new to the project, this is the most useful order to read the files:

- `src/orchestrator.py`  
  This is the main orchestration layer. It is the best place to understand the overall flow.

- `src/firecrawl_client.py`  
  This file contains the browser automation logic and the page-fetching actions.

- `src/ai_extractor.py`  
  This file contains the Gemini prompt logic for page reconnaissance, filter analysis, pagination detection, and record extraction.

- `src/excel_exporter.py`  
  Saves structured output to Excel.

- `src/pdf_downloader.py`  
  Downloads PDFs for matched records.

- `src/main.py`  
  Small runner of orchestrator.

- `src/test_orchestrator.py`  
  Small runner for testing the orchestrator flow.

- `data/`  
  Sample HTML, markdown, debug dumps, and cached pagination results used during development will be stored here.

- `tests/manual_tests`
  Small tests runners used during development. No need to read them, but may help to understand a few concepts.

---

## How the application should work

The real flow is mostly orchestrated by `Orchestrator`.

### 1. Page reconnaissance

`page_sample()` fetches the page and asks the AI to understand:

- what the page is about
- which data fields appear to exist
- which fields are likely filterable
- whether records repeat on the page
- whether pagination may be needed

This is the “understand the page before acting on it” phase.

### 2. Filter discovery

`search_for_filters()` inspects the page HTML and tries to locate the correct field the user wants to filter by.

This step is harder than it sounds because page filters often use:

- custom search boxes
- dropdowns
- checkbox groups
- AG Grid filters
- React-controlled inputs
- hidden or disabled elements

### 3. Script resolution

`resolve_scripts()` tries to recover the JavaScript needed to:

- open a filter
- type a value
- select an option
- trigger the UI update
- wait for the page to reload data

This is the most fragile part of the system. It relies on generated JavaScript snippets that must work against the actual DOM of the target page.

### 4. Filtered data fetch

`filtered_scraping()` applies the resolved logic to each product value and captures the filtered page result.

### 5. Record extraction

`extract_data()` sends the page content to the AI and asks it to return structured records matching the user prompt.

This step decides what the final data looks like: product number, category, price, package, etc.

### 6. Pagination loop

If more pages are available, the code tries to continue fetching results until the list is complete.

This is still one of the weakest parts of the repo because many catalog grids implement pagination in unusual ways.

### 7. Export

The records are flattened and saved to Excel via `ExcelExporter`. If the user requests it, PDF datasheets are also downloaded with `PDFDownloader`.

---

## Developer onboarding: how to run it

### 1. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

The project expects credentials for both:

- Firecrawl
- Gemini

Example:

```bash
export FIRECRAWL_API_KEY="your_firecrawl_key"
export GEMINI_API_KEY="your_gemini_key"
```

Or create a `.env` file:

```env
FIRECRAWL_API_KEY=your_firecrawl_key
GEMINI_API_KEY=your_gemini_key
```

### 4. Run the project

The main prototype entrypoint is:

```bash
python src/main.py
```

The more important application pattern is the orchestrator flow, where a user provides:

- the product page URL
- search values
- a filter field name
- extraction prompt
- output path
- optional PDF download choice

---

## Typical flow in the interactive app

When the app is used interactively, the expected sequence is:

1. paste a catalog URL
2. review the page summary
3. provide product values manually or from a CSV/TXT/XLSX file
4. choose the field to filter by, for example `Product number`
5. optionally add hints for ambiguous field names
6. describe which product attributes to extract
7. choose an output file path
8. decide whether to download datasheets

Example value list:

```text
TMP75, TMP117, ISOTMP35
```

Example filter input:

- field: `Product number`
- hint 1: `part number`
- hint 2: `filter by product`

The app then resolves the filter interaction, fetches the matching results, and extracts product records.

---

## What is already working

The project already demonstrates a real concept that works in a useful subset of cases:

- scraping pages with browser automation
- understanding product pages using AI
- resolving dynamic search/filter interactions
- extracting structured product records
- exporting data to Excel
- downloading PDFs when data is available

That means the repo is valid as a prototype and useful as a technical foundation.

---

## Most important technical rules for contributors

Before changing behavior in this repo, understand these rules:

- AI output is not trusted blindly; it must be validated
- the generated JavaScript must be carefully scoped to avoid collisions
- selectors should not rely only on generic repeated CSS classes
- React-controlled inputs often ignore plain value assignment and need a workaround
- pagination and filter logic are highly dependent on the target website structure
- API rate limits and flaky browser behavior require retries and backoff

These are the main reasons the project is tricky and why some site-specific debugging is necessary.

---

## Known limitations / imperfections

This is one of the most important sections for anyone joining the project.

### 1. The filtering layer is not fully general

The code can handle several patterns, but it is not yet a perfect universal scraper for all catalog sites. Some logic is still site-specific.

### 2. AI-based selector resolution is not deterministic

The model may occasionally choose a stale or incorrect selector. This is one of the biggest quality issues in the project today.

### 3. Pagination is still unreliable in some cases

Pages with virtualized grids, hidden counters, or custom JS navigation still require custom behavior.

### 4. Some filters are still missing

Range-slider or other specialized filter controls are not fully supported yet.

### 5. Some code is intentionally experimental

There are prototype scripts, debug output, and old helper logic still present.

---

## What still needs to be developed

This is the roadmap for the next phase of the project.

### High priority

1. Generalize filter logic across more sites
   - support more filter types
   - reduce site-specific patches
   - improve selector reliability

2. Improve pagination handling
   - detect page count more safely
   - support virtualized grids better
   - handle more next-page patterns

3. Harden the AI contract
   - validate JSON more strictly
   - add clearer retry logic
   - handle malformed model output better

4. More user friendly design
   - better error messages and diagnostics
   - more robust handling of missing filters
   - easier handling of ambiguous field names

5. Add automated tests
   - unit tests for helpers
   - fixture-based tests with sample HTML pages
   - regression coverage for known working patterns

6. Reduce hardcoded behavior
   - move repeated fixes into reusable abstractions
   - continue converting custom hacks into general logic

### Medium priority

1. better caching for resolved selectors and pagination flow
2. more robust handling of rate limits
3. better PDF validation and download reliability
4. improved record deduplication
5. export support beyond Excel

### Nice to have

1. dashboard interface for running jobs
2. batch queue support
3. better audit logs and provenance
4. richer diagnostics when a page fails
5. packaging for cleaner installation and reuse

---

## Best way to debug this repo

When a page fails, the most effective debugging flow is:

1. save scraped html/markdown to file in `data/` and inspect it or inspect wanted html elements in browser
2. check whether the filter is present and visible
3. inspect the orchestrator step that failed
4. verify whether the generated JavaScript is valid in the browser context
5. inspect the AI output and confirm the structure before trusting it

A large amount of the project’s issues are not Python exceptions; they are DOM/state issues caused by dynamic websites.

---

## Bottom line

This repository is a valid prototype for AI-assisted scraping of dynamic product catalogs, but it is still under active development.

The biggest engineering tasks are:

- making filter resolution more robust
- improving pagination for complex grids
- generalizing across more websites
- reducing site-specific hacks
- adding tests and more reliable validation

If you are a new developer, the correct place to start is the orchestrator flow, then Firecrawl, then Gemini, and only then (optional) the older helper code or experimental scripts.

---

## Quick summary

- Main orchestration: `src/orchestrator.py`
- Browser automation: `src/firecrawl_client.py`
- AI extraction: `src/ai_extractor.py`
- Excel export: `src/excel_exporter.py`
- PDF download: `src/pdf_downloader.py`
- Runner script: `src/main.py`
- Main rough spots: dynamic filters, selector reliability, user friendly ui, pagination, and site generalization
