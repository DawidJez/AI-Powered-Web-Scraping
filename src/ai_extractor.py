import math
from pydoc import html

from google import genai
from google.genai import types
from google.genai import errors

import os
import json
import re
import random
from bs4 import BeautifulSoup
import time
from concurrent.futures import ThreadPoolExecutor, as_completed # maybe used in the future
from markdownify import markdownify

class AIExtractor:
    RETRY_ATTEMPTS = 5
    RETRY_BASE_SECONDS = 4

    def _backoff(self, attempt: int) -> float:
        """Free-tier Gemini rejects in bursts, so back off long enough for the burst to pass."""
        return self.RETRY_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 1)

    def __init__(self, api_key: str | None = None):
        self.client = genai.Client(api_key=api_key or os.getenv("GEMINI_API_KEY"))
        self.tokens_usage: list[dict] = []
        self.model: str = "gemini-3.1-flash-lite"
        # DEBUG INFO: use model: "gemini-flash-latest"/"gemini-2.5-flash"/"gemini-2.5-flash-lite" 
        # after "gemini-3.1-flash-lite" is burnt out of tokens

    def _window_score(self, fragment: str) -> int:
        """
        How likely a fragment holds an actual filter control rather than just the words.
        A generic term ("Type", "N/A") also matches table headers and body cells, and those
        fragments are useless for resolving a filter - this is what ranks them lower.
        """
        score = 0
        lowered = fragment.lower()

        for marker in ['type="checkbox"', 'type="radio"', "<select", "<input", "facet", "filter"]:
            if marker in lowered:
                score += 1

        return score

    def _extract_html_windows(self, html: str, search_terms: list, window_size: int = 3000, word_count: int = 3, max_windows_per_term = 20, max_total_chars: int = 120000) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")

        for trash_tag in soup(["script", "style"]):
            trash_tag.decompose()
        clean_html = str(soup)

        candidates = []
        for search_term in search_terms:
            short_term = " ".join(search_term.split()[:word_count])
            if not short_term:
                continue

            pattern = re.compile(r'(?<![A-Za-z0-9])' + re.escape(short_term) + r'(?![A-Za-z0-9])')

            start_idx = 0
            term_candidates = []
            # scan wider than we keep, so the ranking below has something to choose from
            while len(clean_html) > start_idx and len(term_candidates) < max_windows_per_term * 3:
                m = pattern.search(clean_html, start_idx)
                if not m:
                    break

                idx = m.start()

                start = max(0, idx - window_size)
                end = min(len(clean_html), idx + len(short_term) + window_size)
                fragment = clean_html[start:end]

                term_candidates.append({
                    "position": start,
                    "fragment": fragment,
                    "score": self._window_score(fragment),
                })

                start_idx = end

            term_candidates.sort(key=lambda w: (-w["score"], w["position"]))
            candidates.extend(term_candidates[:max_windows_per_term])

        candidates.sort(key=lambda w: (-w["score"], w["position"]))

        kept = []
        total_chars = 0

        for candidate in candidates:
            if total_chars + len(candidate["fragment"]) > max_total_chars:
                continue

            kept.append(candidate)
            total_chars += len(candidate["fragment"])

        kept.sort(key=lambda w: w["position"])

        return [{"occurrence_index": i, "fragment": w["fragment"]} for i, w in enumerate(kept)]

    def _json_object(self, response_text: str, fallback=None):
        """One shape for every model reply: a dict, or the fallback when it isn't one."""
        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError:
            print(f"DEBUG: failed to parse the response: {response_text[:300]}")
            return fallback

        # the model sometimes wraps the object in a JSON array
        if isinstance(parsed, list):
            parsed = next((p for p in parsed if isinstance(p, dict)), None)

        if not isinstance(parsed, dict):
            print(f"DEBUG: unexpected response shape: {response_text[:300]}")
            return fallback

        return parsed

    def _chunk_segmentation(self, load: str, chunk_size: int, overlap: int = 2000) -> list[str]:
        chunks = []
        start = 0

        while start < len(load):
            end = start + chunk_size
            chunks.append(load[start:end])
            start = end - overlap # overlap needed if record would be cut in half during chunking
        
        return chunks
    
    def _deduplicate(self, records: list[dict]) -> list[dict]:
        seen = set()
        unique = []

        for r in records:
            key = json.dumps(r, sort_keys=True)
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique

    def _detect_total_records(self, markdown_content: str, max_retries: int = 3) -> int | None:
        system_instruction = """
            Look at the page content and find a total item/result count if one is
            visible (e.g. "1 - 25 of 58 items", "58 results", "Showing 58 products").
            Return only valid JSON: {"total_records_declared": integer or null}
            If no such count is visible, return {"total_records_declared": null}.
        """

        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=[f"Content:\n{markdown_content[:3000]}"],
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json"
                    )
                )
                parsed = json.loads(response.text)
                return parsed.get("total_records_declared")
            except (errors.ServerError, errors.ClientError, json.JSONDecodeError) as e:
                print(f"DEBUG: total_records detection failed (attempt {attempt}): {e}")
                if attempt == max_retries:
                    return None
                time.sleep(self._backoff(attempt))
        return None

    def _single_extract(self, content: dict, user_prompt: str, fields_instruction: str, max_retries: int = RETRY_ATTEMPTS):

        markdown_content = content.get("markdown")
        if not markdown_content:
            markdown_content = markdownify(content["html"])

        system_instruction = ("""
            # Role
            You are an assistant specialized in extracting structured data from scraped web pages
            that have been converted to Markdown, including pages built on JS data-grid components
            (e.g. AG Grid), where the same record may be rendered in two separate places:
            1. A compact entry near the top of the document (an identifier/name, often as a link,
            sometimes with a status word and datasheet-style links).
            2. A separate, more detailed block of values further down the document, listing that
            record's full field values.

            # Key challenge
            The order of fields within a detailed value block does NOT necessarily match the order
            of any column/field list declared elsewhere on the page (e.g. in a header or filter panel).
            Do NOT assume positional field order from any declared column list.

            Instead:
            - Match each compact entry to its corresponding detailed block by their RELATIVE ORDER
            in the document: the Nth compact entry (top to bottom) corresponds to the Nth detailed
            block (top to bottom), assuming both compact entries and detailed blocks appear in the
            same relative sequence.
            - Within each detailed block, infer the MEANING of each value from its format/content,
            not its position. For example: a value starting with "$" is a price; a value containing
            "°C" or written as "NUMBER to NUMBER" is a temperature range; a value in mm or mm²
            (or containing "x"/"×" between two numbers) is a physical dimension; a short
            alphanumeric code (e.g. "SOIC", "VQFN-HR") is a package type; a single small integer is
            likely a pin count; a word like "ACTIVE", "PREVIEW", "OBSOLETE" is a status; a tag like
            "Catalog", "Automotive", "Industrial", "Military" is a rating/qualification tier; an em
            dash "—" or empty value means the field is not applicable/not available for this record.

            # Field naming contract - CRITICAL
            A "canonical field list" may be provided below (fields_instruction). If it is present, it
            is BINDING and takes priority over anything you see on the page:
            - Every record's JSON keys MUST come EXACTLY from that list - same spelling, same casing,
            same punctuation/units in the key name (e.g. if the list says "MPN", never emit "Product
            number", "Part Number", or any other synonym you happen to see as a column header on THIS
            particular page).
            - Recognize when a value you find on the page semantically matches one of the canonical
            fields even if the page's own label differs (e.g. a page column literally labeled
            "Product Number" still maps to the canonical key "MPN" if that's what's in the list) -
            map by MEANING, not by copying the page's literal label.
            - Never invent a new key that isn't in the canonical list, and never emit two different
            keys for what is semantically the same field.
            - If a canonical field has no value findable for a given record, set it to null - do not
            omit the key and do not substitute a differently-named key instead.
            - If NO canonical field list is provided, fall back to using keys matching the user's
            requested field names from their own prompt instead.

            # Task
            Given the user's field request and the page content described above, extract the
            requested fields for matching records, using the field list declared elsewhere on the
            page (if present) only as a HINT for what fields exist and their expected format -
            never as a guarantee of value order within a detail block.

            Find ALL matching records - do not stop after the first match, and do not stop early
            even if you also need to report pagination info below. Record extraction is the
            PRIMARY task and must be fully exhaustive - go through the entire content from top to
            bottom and extract every single matching record before moving on to anything else. If a
            field cannot be confidently determined for a record, set it to null - never guess or
            fabricate a value.

            # Pagination detection
            AFTER you have finished extracting every record above, separately check if the page
            shows a page counter (e.g. "Page 1 of 3", "1-50 of 128") anywhere in the content. This
            is a SECONDARY, independent task - it must never cause you to skip, truncate, or stop
            extracting records early. If a counter is found, report the total number of pages as
            "filtered_page_count" (an integer). If no such counter is visible, set
            "filtered_page_count" to null - do not guess.

            From the same counter, also report the total number of records it states (the 128 in
            "1-50 of 128") as "total_records_declared" - the total for the CURRENT, possibly
            filtered view, never a number you remember from elsewhere. Null if not visible.

            # Response Format
            Return only valid JSON - a single JSON object, no explanations, no Markdown
            formatting, no extra text:
            {
            "records": [ ... array of record objects as described above ... ],
            "filtered_page_count": integer or null,
            "total_records_declared": integer or null
            }
            If no records match, "records" must be [].
        """)

        # it is told that free gemini api version is more susceptible for server errors
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=[f"fields_instruction (canonical JSON keys - see Field naming contract):\n{fields_instruction}\n\nSearching for: {user_prompt}\n\nContent:\n{markdown_content}"],
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json"
                    )
                )
                metadata = response.usage_metadata

                self.tokens_usage.append({
                    "prompt_tokens": metadata.prompt_token_count,
                    "output_tokens": metadata.candidates_token_count,
                    "total_tokens": metadata.total_token_count
                })
                
                break
            
            except errors.ServerError as e:
                wait = self._backoff(attempt)
                print(f"DEBUG: ServerError ({e}), retry {attempt}/{max_retries} in {wait}s")

                if attempt == max_retries:
                    print("DEBUG: attempts exhausted, returning an empty result")
                    return {"records": [], "filtered_page_count": None, "total_records_declared": None}

                time.sleep(wait)

            except errors.ClientError as e:
                if getattr(e, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(e):
                    wait = 60
                    print(f"DEBUG: 429 RESOURCE_EXHAUSTED, retry {attempt}/{max_retries} in {wait}s")
                    if attempt == max_retries:
                        return {"records": [], "filtered_page_count": None, "total_records_declared": None}
                    time.sleep(wait)
                    continue
                raise
            
        try:
            parsed = json.loads(response.text)
        except json.JSONDecodeError:
            if attempt == max_retries:
                print(f"Failed attempts: {max_retries} for parsing JSON")
                return {"records": [], "filtered_page_count": None, "total_records_declared": None}
            print(f"DEBUG: failed to parse JSON response: {response.text[:300]!r}")
            return {"records": [], "filtered_page_count": None, "total_records_declared": None}

        if not isinstance(parsed, dict) or "records" not in parsed:
            print(f"DEBUG: model returned wrong format (attempt {attempt}/{max_retries}), expected dict with 'records', got: {type(parsed)}")

            if attempt == max_retries:
                print("DEBUG: attempts exhausted, returning empty result")

                return {"records": [], "filtered_page_count": None, "total_records_declared": None}

        return parsed
        
    def extract(self, filtered_data: dict, user_prompt: str, available_fields: list[str]) -> dict:
        fields_instruction = (
            "You MUST use exactly these field names as JSON keys for every record, "
            "verbatim, no renaming, no snake_case conversion, no synonyms:\n"
            + "\n".join(f"- {f}" for f in available_fields)
            + "\n- MPN\n"
            "- Full URL of the PDF datasheet\n"
            "\nAlways include the last two fields above (MPN, Full URL of the PDF datasheet) "
            "under EXACTLY those names, even if they aren't in the main field list and even if "
            "they only appear near the record's name/identifier rather than in the main field "
            "list - never rename them to a synonym you see on the page. "
            "\nIf a field's value isn't present for a given record, use null - "
            "never omit the key and never invent a new key not in this list."
        )

        result = self._single_extract(filtered_data, user_prompt, fields_instruction)

        # every failure path is supposed to answer with the empty result shape - don't trust it blindly
        if not isinstance(result, dict):
            result = {"records": [], "filtered_page_count": None, "total_records_declared": None}

        records = self._deduplicate(result.get("records", []))
        page_count = result.get("filtered_page_count")

        if not page_count:
            markdown_content = filtered_data.get("markdown") or markdownify(filtered_data["html"])
            total_declared = self._detect_total_records(markdown_content)

            if total_declared and records:
                page_count = math.ceil(total_declared / len(records))

        return {
            "records": records,
            "paging_needed": bool(page_count and page_count > 1),
            "page_count": page_count,
            "total_records_declared": result.get("total_records_declared")
        }

    def suggestion(self, content: str, max_retries: int = RETRY_ATTEMPTS):

        system_instruction = ("""
            # Role
            You are a reconnaissance assistant that analyzes ONE page of scraped web content
            (markdown) and reports what kind of data extraction is possible on it, WITHOUT
            extracting any actual records yet.

            # Task
            Examine the content and determine:
            1. What kind of repeating entity/record the page represents (e.g. "product",
            "job listing", "article", "company profile"). If the page does not appear to
            contain a repeating list of similar items, say so.
            2. What fields/attributes appear to be available for each entity, based on visible
            column headers, filter panels, labeled values, or repeated patterns in the text.
            List only fields you actually observe evidence of - never invent plausible-sounding
            fields that are not backed by the content.
            
            IMPORTANT: A page's main table/grid header row (the row of column names
            directly above the data rows) is the PRIMARY source of truth for
            available_fields. A secondary "manage columns" or "column chooser" panel,
            if present, is a SUPPLEMENTARY source used only to enrich or add fields -
            never to exclude a field that already appears in the main header row.

            Pinned/mandatory identifier columns (e.g. "Product number", "Part number",
            "SKU", "Model number") are frequently excluded from "manage columns" panels
            because the UI does not allow hiding them - this does NOT mean they lack a
            filter. If such a field appears in the main header row and there is a
            visible search/filter input anywhere on the page (even a generic search box
            whose target is not explicitly labeled), assume it filters on that
            identifier field and include it in filter_options with cardinality
            "identifier".
                              
            When the main header row and the columns panel disagree, take the UNION of
            both, never the intersection.
                              
            If a field name in the header row and a field name in the columns panel
            appear to refer to the same underlying data (e.g. slightly different
            labels for what is clearly the same column position/content), treat them
            as one field using the header row's name - do not list both as separate
            fields.
                              
            3. Whether the page declares a total record count and/or a per-page record count
            (e.g. phrases like "396 results", "1 to 50 of 396", "Page 1 of 8"). Extract these
            numbers if present.
            4. Whether the page appears to support pagination, and if so, what kind:
            - "url_param": you see evidence of page/offset numbers likely tied to the URL
            - "click_button": you see button/link text suggestive of manual pagination
                (e.g. "Next", "Load more", page number links)
            - "infinite_scroll": no visible pagination control, but the record count suggests
                more content than currently shown
            - "none": no evidence of more records beyond what's shown
            - "unknown": not enough evidence to tell
            Report the exact text/label you saw as evidence (e.g. the literal button text),
            not a guessed CSS selector.
            5. Whether the page appears to support filtering/narrowing results before viewing
            all records, and if so, what kind:
            - "url_param": you see evidence that filters might be expressed as URL query
                parameters (e.g. visible example links with query strings)
            - "ui_interaction": you see filter controls (dropdowns, checkboxes, filter panel
                labels) that would need to be interacted with, not URL-based
            - "none": no filtering capability observed
            - "unknown": not enough evidence to tell
            List the filter names/categories you observed (e.g. "Subcategory", "Rating"),
            and example values if visible.
                              
            IMPORTANT: In grid/table layouts, it is common for EVERY column to have its
            own filter (e.g. a floating filter input below each header, even if not
            visually prominent, or a per-column entry in a "manage columns" panel with
            filtering enabled). As a default assumption, treat every field listed in
            available_fields as ALSO having a corresponding filter in filter_options,
            unless you see clear evidence that a specific field does NOT have one. Do
            not require strong/explicit proof of a filter's existence before including
            it - the default expectation is that it exists.

            6. Whether the page offers a bulk/batch search feature - a way to submit multiple
            identifiers at once (e.g. "paste part numbers", "BOM search", "cross-reference
            tool", a multi-line text input for search terms). This is different from a normal
            single-term search box. Report the literal text/label if found, or false if not found.
            7. A short, friendly, human-readable recap of everything above, written for a
            non-technical user (not a developer). Summarize in plain language: what kind of
            data was found, roughly how much of it there is, what fields/filters are available,
            and any relevant heads-up (e.g. many pages to go through, uncertainty in detection).
            Avoid all technical jargon (no "selector", "URL param", "pagination type", "HTML",
            "API", etc.) - describe things the way you'd explain them to a curious friend.
            End this recap with a short, lightly playful line indicating you're ready and
            waiting for the user's next instruction (e.g. what to extract, or how to narrow
            things down) - even though you are not an autonomous agent and won't act on your
            own, a small friendly nod to "standing by" is welcome here.
                                                            
            # Rules
            - Base every answer strictly on evidence present in the provided content.
            - If something cannot be determined from this single page, use null or an empty
            list/string rather than guessing.
            - Do not fabricate CSS selectors, URLs, or field values that are not directly visible.
            - Be conservative: it is better to report "unknown" than to report a confident
            answer that isn't supported by the text.
            - Keep "notes" technical/internal (for developers) and "non_dev_summary" friendly/
            plain-language (for end users) - do not mix the two styles.

            # Response Format
            Return only valid JSON matching this exact structure, with no extra text:
            {
            "entity_type": string or null,
            "available_fields": [string, ...],
            "total_records_declared": number or null,
            "records_per_page_declared": number or null,
            "pagination_detected": boolean,
            "pagination_type": "url_param" | "click_button" | "infinite_scroll" | "none" | "unknown",
            "pagination_evidence": string or null,
            "filter_detected": boolean,
            "filter_type": "url_param" | "ui_interaction" | "none" | "unknown",
            "filter_options": [{"name": string, "cardinality": "identifier" | "category", "example_values": [string, ...] or null}],
            "notes": string,
            "non_dev_summary": string
            }
        """)

        response = None
        for attempt in range(1, max_retries + 1):

            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=[f"Content:\n{content}"],
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json"
                    )
                )

                metadata = response.usage_metadata

                self.tokens_usage.append({
                    "prompt_tokens": metadata.prompt_token_count,
                    "output_tokens": metadata.candidates_token_count,
                    "total_tokens": metadata.total_token_count
                })
                break

            except errors.ServerError as e:
                wait_time = self._backoff(attempt)

                print(f"DEBUG: ServerError ({e}), retry {attempt}/{max_retries} in {wait_time}s")
                # nothing downstream works without the page probe, so stop here instead of
                # letting an empty result surface later as a filter problem
                if attempt == max_retries:
                    raise RuntimeError(
                        f"The AI model is unavailable right now, so the page could not be analysed "
                        f"after {max_retries} attempts. Please try again in a few minutes. Reason: {e}"
                    )
                
                time.sleep(wait_time)

        return self._json_object(response.text, {})
        

    def analize_html(self, html: str, search_terms: list, max_retries: int = RETRY_ATTEMPTS):
        #print(search_terms)
        search_terms = [s for s in search_terms if s and s.strip()]
        #print(f"html: {len(html)} i search_terms ({len(search_terms)}): {search_terms}")
        
        html_windows = self._extract_html_windows(html, search_terms)

        if not html_windows:
            return {
                "chosen_occurrence_index": None,
                "gate_script": None,
                "gate_needed": False,
                "target_script": None,
                "available_options": [],
                "interaction_type": "unknown",
                "confidence": "none",
                "evidence": "text not found in HTML"
            }
        
        combined_windows = "\n\n".join(
            f"--- Occurrence {w['occurrence_index']} ---\n{w['fragment']}"
            for w in html_windows
        )

        with open("data/shit/html_windows.html", "w", encoding="utf-8") as f:
            f.write(combined_windows)
        
        if len(combined_windows) > 150000:
            raise ValueError(f"DEBUG: MAYDAY we got a problem with length {len(combined_windows)}")
        
        system_instruction = ("""
            # Role
            You're given HTML fragments, each a different occurrence of a target field's label. Identify
            the full interaction path needed to filter by it — not just one element.

            # Gate vs target
            A control may be directly interactable, or hidden behind a GATE (a button/icon that must be
            clicked first to reveal it). A fragment is either the field's OWN control (label/name/aria
            matches), or a GENERIC gate shared across many fields — never treat a generic gate as the
            control itself. If a page has MULTIPLE possible gates for the same field, prefer whichever one
            leads to a real, working, non-disabled control — don't stop at the first gate you see if its
            target turns out to be a decoy per the Critical Priority Rule below.

            # Task — for the target field, determine:
            1. target_script: the field's own control, if a fragment shows it.
            2. gate_script: a gate that must be clicked first, if any (null if not needed).
            3. interaction_type: "type" | "click_checkbox" | "click_option" | "select_dropdown" | "unknown"

            # Writing scripts
            Short standalone JS run via executeJavascript (`document` available directly). Always wrap in
            an IIFE: `(function(){ ... })();` (prevents "Identifier already declared" when multiple scripts
            run in the same page context). Use querySelector/querySelectorAll with real CSS, or
            `[...document.querySelectorAll('CONTAINER')].find(el => el.textContent.includes('TEXT'))` +
            `.closest(...)` for text-based matching. For shadow DOM elements (hyphenated custom tags, e.g.
            `<ti-search-field>`), you MUST go through `.shadowRoot.querySelector(...)` — a plain descendant
            selector never reaches inside a shadow root. Never use jQuery pseudo-selectors (:contains(),
            :has(), :eq()) — invalid CSS, throws at runtime. Use `.textContent.includes()` + `.closest()`
            instead. Final statement must perform the interaction (`.click()`, or for "type", the final
            dispatched event) — not just locate the element.

            Guard every element lookup before interacting — `const el = document.getElementById('...'); if (el) { el.click(); }`
            — never call a method directly on a possibly-null result. A crashed action kills the ENTIRE
            action sequence including unrelated later steps, so a silent no-op on a bad guess is far safer
            than throwing. Applies to gate, target, and every available_options script.
            
            **ID selectors with special characters** — element IDs containing `:`, `.`, or other CSS-special
            characters (common in JSF/PrimeFaces components, e.g. `id="CC02:j_id_5v_3:6:j_id_5v_u_1"`) MUST
            use `document.getElementById('literal:id:here')` — never `document.querySelector('#literal\\:id...')`.
            querySelector requires the CSS escape to survive as a literal backslash at runtime, but a single
            backslash in JS source is silently dropped during string-literal parsing before it ever reaches
            the CSS engine, producing an invalid-selector error. getElementById takes the ID as a plain string
            with no CSS parsing, so no escaping is needed or wanted.

            **"type" interactions** — the script must perform the FULL text entry itself (not just focus).
            Use a native setter (so React/framework-controlled inputs register the change) + dispatched
            events with `composed: true` on all of them (required to cross a Shadow DOM boundary):

            ```js
            (function() {
                const input = /* scoped selector */;
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                input.focus();
                setter.call(input, "__VALUE__");
                input.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                input.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                input.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, composed: true, key: 'Enter' }));
                input.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, composed: true, key: 'Enter' }));
            })();
            ```
            Use the literal string `"__VALUE__"` as a placeholder — never invent a real value; it's
            substituted by code after you return.

            # Scoping repeated controls (grids like AG Grid)
            Grids often render the SAME control once per column with identical attributes (same class,
            aria-label) — an unscoped selector returns the wrong column. Scope via a shared positional
            attribute (e.g. `aria-colindex`) linking the header to its control:
            ```js
            (function() {
                const header = [...document.querySelectorAll('th')].find(th => th.textContent.includes('FIELD'));
                const idx = header.getAttribute('aria-colindex');
                const cell = document.querySelector(`[aria-colindex="${idx}"]`);
                cell.querySelector('SELECTOR').click();
            })();
            ```
            This applies to the gate AND to any target/option script touching a repeated-class element —
            never scope by a positional index (nth-child, [N]) alone; always locate via the header's own
            text match first, exactly like the example above. Skip this scoping only when the element
            already has its own unique, literal attribute (id, placeholder) that doesn't repeat elsewhere.

            # MULTIPLE OPTIONS MODE (click_checkbox / click_option / select_dropdown)
            Don't return one target_script — find ALL selectable options and report each as
            `{"value": ..., "script": ..., "selector": ...}` (a script clicking that SPECIFIC option, never
            a shared template) in `available_options`; leave `target_script` null. Note in "evidence" if the
            option set looks partial (e.g. scrolled).

            # NATIVE CLICK SELECTOR — fill `selector` whenever you can
            Some frameworks ignore scripted clicks entirely and only react to a real browser click, which is
            driven by a plain CSS selector instead of JS. So for every option also return `selector`: the CSS
            selector of the SAME element, with no JS around it, e.g.
            `th:nth-child(1) select.FilterOption option[value="ABC"]`. CSS cannot match text, so here a
            positional index IS allowed when nothing else identifies the element — prefer a unique id or
            attribute whenever one exists. It must be plain CSS that `document.querySelector` accepts:
            never `:has-text(...)`, `:contains(...)`, `:visible`, `>>` or XPath — those are testing-tool
            extensions and no browser parses them. CSS cannot match text at all, so when the element has
            to be scoped to one column or row, scope it by an attribute that identifies that column in
            the fragment — `aria-colindex`, `col-id`, `data-field`, `data-colindex` — or by position, e.g.
            `[aria-colindex="3"] button[ref="eButtonShowMainFilter"]`. Read the actual value out of the
            HTML you were given; never approximate the scope with a text-matching pseudo-class. Return null only when the element cannot be addressed
            by a selector at all (it lives in Shadow DOM, or it appears only after another script runs).

            The GATE needs one too, as `gate_selector`: menus built by grid/framework components often
            ignore a scripted click and open only on a real one, so the gate is clicked by selector
            whenever you provide it.

            # APPLY BUTTON
            Some sites need an explicit "Apply"/"Search"/"Update Results" click after selecting checkboxes
            before the filter takes effect. If visible, report it in `apply_script` (one script, shared
            across the whole panel — not per-option). Otherwise null.

            # SELECT ELEMENT SUBTYPE (when interaction_type is select_dropdown)
            Check for framework-controlled signs (hashed class names like `Component_xY12z`, data-reactroot/
            data-reactid). If present, plain `.value=`/`.click()` is silently ignored — use a native setter
            first, then dispatch:
            ```js
            (function() {
                const select = /* scoped selector */;
                const option = Array.from(select.options).find(o => o.value === 'TARGET_VALUE');
                const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
                setter.call(select, 'TARGET_VALUE');
                select.dispatchEvent(new Event('change', { bubbles: true }));
                option.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
            })();
            ```
            If no framework signs, plain `select.value = 'TARGET_VALUE'` + `dispatchEvent('change')` (single
            dropdown) or `option.click()` (multi-select listbox) is enough.

            # CRITICAL PRIORITY RULE — disabled twins vs real web components
            A custom element (hyphenated tag, e.g. `<ti-search-field>`) often wraps the REAL control in
            Shadow DOM, while a nearby plain `<input>`/`<select>`/`<button>` "twin" (disabled, zero-size,
            internal to a grid library) may share a similar label but is NOT the real control. Disabled/
            hidden state ALWAYS disqualifies an element, no matter how well its label matches — a visible
            custom element with even a loosely-matching placeholder wins (e.g. placeholder "Filter by
            product or part number" is enough evidence for a "Product number" field). If the disabled twin
            is the ONLY thing visible in these fragments, return null for target_script and gate_needed=true
            instead of falling back to it. This applies to every interaction_type, including "type".

            # Response Format
            Return only valid JSON:
            {
            "chosen_occurrence_index": integer or null,
            "gate_script": string or null,
            "gate_selector": string or null,
            "gate_needed": boolean,
            "target_script": string or null,
            "available_options": [{"value": string, "script": string, "selector": string or null}, ...],
            "apply_script": string or null,
            "interaction_type": "type" | "click_checkbox" | "click_option" | "select_dropdown" | "unknown",
            "confidence": "high" | "low" | "none",
            "evidence": string
            }
        """)

        response = None
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=[f"Target label: {search_terms}\n\nHTML fragment:\n{combined_windows}"],
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json"
                    )
                )
                
                metadata = response.usage_metadata
                self.tokens_usage.append({
                    "prompt_tokens": metadata.prompt_token_count,
                    "output_tokens": metadata.candidates_token_count,
                    "total_tokens": metadata.total_token_count
                })
                break

            except errors.ServerError as e:
                wait_time = self._backoff(attempt)

                print(f"DEBUG: ServerError ({e}), retry {attempt}/{max_retries} in {wait_time}s")
                if attempt == max_retries:
                    print("DEBUG: The number of attempts has been exhausted; an empty response is returned.")
                    return {}
                
                time.sleep(wait_time)

        return self._json_object(response.text, {})

    def find_global_apply_script(self, html: str, max_retries: int = RETRY_ATTEMPTS):
        """
        Fallback for sites where the per-filter resolver never sees the real Apply button
        because it lives outside that filter's HTML window (e.g. a shared modal footer).
        """
        html_windows = self._extract_html_windows(html, ["Apply Filters", "Apply"])

        if not html_windows:
            return {"apply_script": None, "confidence": "none", "evidence": "text not found in HTML"}

        combined_windows = "\n\n".join(
            f"--- Occurrence {w['occurrence_index']} ---\n{w['fragment']}"
            for w in html_windows
        )

        system_instruction = ("""
            # Role
            Find the ONE global "Apply"/"Apply Filters"/"Update Results" button that commits ALL
            currently-selected filters at once (usually in a modal footer or filter panel header).

            # Critical distinction
            Pages with per-category filter groups often repeat a small "Apply" link inside EACH
            category's own panel (one per category, always the same class/text) — these are NOT
            what we want. The real target is the single, page-level button that submits the whole
            filter set — often has a distinguishing class/state (e.g. "enabled") or fuller text
            (e.g. "Apply Filters" vs the repeated bare "Apply"). If several near-identical
            "Apply" elements exist and none stands out, prefer the LAST one in document order if it
            has a fuller/more specific text than the repeated ones — modals commonly place the real
            submit button at the end, after all category panels.

            # Writing scripts
            Short standalone JS run via executeJavascript (`document` available directly). Wrap in
            an IIFE. Use querySelectorAll + a distinguishing class/text match — never a positional
            index alone if several near-identical elements exist. Guard against null:
            `const el = ...; if (el) { el.click(); }`.

            # Response Format
            Return only valid JSON:
            {
            "apply_script": string or null,
            "confidence": "high" | "low" | "none",
            "evidence": string
            }
        """)

        response = None
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=[f"HTML fragments:\n{combined_windows}"],
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json"
                    )
                )

                metadata = response.usage_metadata
                self.tokens_usage.append({
                    "prompt_tokens": metadata.prompt_token_count,
                    "output_tokens": metadata.candidates_token_count,
                    "total_tokens": metadata.total_token_count
                })
                break

            except errors.ServerError as e:
                wait_time = self._backoff(attempt)
                print(f"DEBUG: ServerError ({e}), retry {attempt}/{max_retries} in {wait_time}s")
                if attempt == max_retries:
                    return {"apply_script": None, "confidence": "none", "evidence": "attempts exhausted"}
                time.sleep(wait_time)

        return self._json_object(response.text, {"apply_script": None, "confidence": "none", "evidence": "JSON parse failure"})

    # debug func when testing filtereing and scrpping the data
    # ai returns only list of found profucts and some short analisys
    def debug_ask_ai_about_products(self, firecrawl_result: dict):

        prompt = f"""
            Masz ponizej surowy markdown wygenerowany ze scrapowania strony
            (moze byc pozlepiany, bez spacji, chaotyczny - to normalne po
            automatycznym scrapowaniu, nie przejmuj sie formatowaniem).

            To jest test/debug, nie potrzebuje structured JSON. Po prostu
            powiedz mi luznym tekstem:

            1. Czy widzisz tu liste produktow / product numberow? Ile ich
            widac w tym fragmencie?
            2. Wypisz WSZYSTKIE product number / kody produktow ktore
            znajdziesz (nawet jesli sa pozlepiane z innym tekstem bez spacji -
            wypisz je oddzielnie mimo to).
            3. Czy widzisz cokolwiek co wyglada na filtr po ktorym tekst
            zostal juz przefiltrowany (np. wpisana fraza w polu wyszukiwania,
            ktora ograniczyla liste)?
            4. Czy cos wyglada podejrzanie / ucietego / zepsutego w tym
            markdownie?

            Content:
            ---
            {firecrawl_result}
            ---
        """

        response = self.client.models.generate_content(
            model=self.model,
            contents=[f"Prompt:\n{prompt}"],
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )

        print("=" * 60)
        print("DEBUG: AI response:")
        print("=" * 60)
        print(response.text)

        return response.text
    
    def find_script(self, scripts: list, filter: dict, html: str, apply_script: str = None, max_retries: int = RETRY_ATTEMPTS) -> dict:
        """we take the filter["name"] so llm will know what its actually looking for"""

        search_terms = filter.get("search_terms") or [filter["name"]]
        html_windows = self._extract_html_windows(html, search_terms)

        if not html_windows:
            return {"script": None, "confidence": "none", "evidence": "text not found in HTML"}
        
        combined_windows = "\n\n".join(
            f"--- Occurrence {w['occurrence_index']} ---\n{w['fragment']}"
            for w in html_windows
        )

        apply_button_section = ""
        if not apply_script:
            apply_button_section = """
            # APPLY BUTTON
            After selecting a checkbox/option/radio value in a filter panel, some sites require
            an explicit separate action to actually apply the filter and reload the data - e.g.
            a button labeled "Apply", "Apply Filters", "Search", "Update Results", or similar,
            usually located near the bottom of the filter panel/facet group. Simply checking the
            box does NOT filter the data on these sites until this button is also clicked.

            If you see such a button in the fragments, report its script in "apply_script"
            using the same scripting rules as above. If no such button is visible, or the
            filter applies immediately on selection, set "apply_script" to null.

            Note: this button is typically shared across the whole filter panel, not
            one-per-option - it should appear only once even in MULTIPLE OPTIONS MODE.
            """

        """ 
        # FOR DEBUG
        import time
        with open(f"data/test_files/combined_windows/debug_windows_{time.time()}.txt", "w", encoding="utf-8") as f:
            f.write(combined_windows)
        """

        system_instruction = ("""
            # Role
            You're given HTML fragments around a target field's name, found AFTER a sequence of clicks
            already opened a filter panel/menu. Determine the NEXT single step to reach a usable filter
            control for this field — and whether that step already IS the final control, or leads deeper
            into a nested sub-panel.

            # Context
            This is one step in a multi-click discovery process. Sometimes the field name inside a panel is
            itself intermediate — clicking it opens a SUB-panel with the real options. Other times it
            directly precedes (or is) the final control.

            # Task
            1. Find the interactive element (input, checkbox, radio, button, role="option"/"button", dropdown
            trigger, etc.) tied to the target field. If several candidates exist, pick the one most clearly
            specific to this field — not a sibling control for a different field.
            2. Classify it: "final_control" (interacting narrows results directly), "intermediate_step"
            (reveals a further sub-panel specific to this field), or "none".
            3. needs_more_depth = true only for "intermediate_step".
            4. interaction_type: "type" | "click_checkbox" | "click_option" | "click_expand" (only valid
            with needs_more_depth=true) | "select_dropdown" | "unknown".

            If multiple candidate gates/controls exist for this field, prefer whichever leads to a real,
            working, non-disabled control — don't settle for the first one if it turns out to be a decoy per
            the Critical Priority Rule below.

            # Writing scripts
            Short standalone JS run via executeJavascript (`document` available directly). Always wrap in
            an IIFE: `(function(){ ... })();` (prevents "Identifier already declared" when scripts run
            sequentially in the same page context). Use querySelector/querySelectorAll with real CSS, or
            `[...document.querySelectorAll('CONTAINER')].find(el => el.textContent.includes('TEXT'))` +
            `.closest(...)` for text matching. Shadow DOM (hyphenated custom tags, e.g. `<ti-search-field>`):
            MUST go through `.shadowRoot.querySelector(...)` — a plain descendant selector never reaches
            inside a shadow root. Never use jQuery pseudo-selectors (:contains(), :has(), :eq()) — invalid
            CSS, throws at runtime. Use `.textContent.includes()` + `.closest()` instead. Final statement
            must perform the interaction (`.click()`/`.focus()`, or for "type", the final dispatched event).

            Guard every element lookup before interacting — `const el = document.getElementById('...'); if (el) { el.click(); }`
            — never call a method directly on a possibly-null result. A crashed action kills the ENTIRE
            action sequence including unrelated later steps, so a silent no-op on a bad guess is far safer
            than throwing. Applies to gate, target, and every available_options script.

            **ID selectors with special characters** — element IDs containing `:`, `.`, or other CSS-special
            characters (common in JSF/PrimeFaces components, e.g. `id="CC02:j_id_5v_3:6:j_id_5v_u_1"`) MUST
            use `document.getElementById('literal:id:here')` — never `document.querySelector('#literal\\:id...')`.
            querySelector requires the CSS escape to survive as a literal backslash at runtime, but a single
            backslash in JS source is silently dropped during string-literal parsing before it ever reaches
            the CSS engine, producing an invalid-selector error. getElementById takes the ID as a plain string
            with no CSS parsing, so no escaping is needed or wanted.

            **"type" interactions** — the script must perform the FULL text entry itself (not just focus).
            Use a native setter (so React/framework-controlled inputs register the change) + dispatched
            events with `composed: true` on all of them (required to cross a Shadow DOM boundary):

            ```js
            (function() {
                const input = /* scoped selector */;
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                input.focus();
                setter.call(input, "__VALUE__");
                input.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                input.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                input.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, composed: true, key: 'Enter' }));
                input.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, composed: true, key: 'Enter' }));
            })();
            ```
            Use the literal string `"__VALUE__"` as a placeholder — never invent a real value; it's
            substituted by code after you return.

            # Scoping repeated controls (grids like AG Grid)
            Grids often render the SAME control once per column with identical attributes (same class,
            aria-label) — an unscoped selector, or an indexed `querySelectorAll(...)[N]`, returns the wrong
            column. Scope via a shared positional attribute (e.g. `aria-colindex`) linking the header to its
            control, or its containing row:
            ```js
            (function() {
                const header = [...document.querySelectorAll('th')].find(th => th.textContent.includes('FIELD'));
                const select = header.querySelector('select.SOME_REPEATED_CLASS');
                const option = Array.from(select.options).find(o => o.value === 'TARGET_VALUE');
                option.click();
            })();
            ```
            Applies to the gate AND to any target/option script touching a repeated-class element — never
            scope by a positional index (nth-child, [N]) alone; always locate via the header's own text
            match first, exactly like the example above. Skip this scoping only when the element already
            has its own unique, literal attribute (id, placeholder) that doesn't repeat elsewhere.

            # MULTIPLE OPTIONS MODE (control_type=final_control AND click_checkbox/click_option/select_dropdown)
            Don't return one script — find ALL selectable options and report each as
            `{"value": ..., "script": ..., "selector": ...}` (a script selecting that SPECIFIC option,
            following all rules above including scoping — never a shared template) in `available_options`;
            leave `script` null. Note in "evidence" if the option set looks partial. For "type", ignore this —
            return a single `script`, empty `available_options`.

            # NATIVE CLICK SELECTOR — fill `selector` whenever you can
            Some frameworks ignore scripted clicks entirely and only react to a real browser click, which is
            driven by a plain CSS selector instead of JS. So for every option also return `selector`: the CSS
            selector of the SAME element, with no JS around it, e.g.
            `th:nth-child(1) select.FilterOption option[value="ABC"]`. CSS cannot match text, so here a
            positional index IS allowed when nothing else identifies the element — prefer a unique id or
            attribute whenever one exists. It must be plain CSS that `document.querySelector` accepts:
            never `:has-text(...)`, `:contains(...)`, `:visible`, `>>` or XPath — those are testing-tool
            extensions and no browser parses them. CSS cannot match text at all, so when the element has
            to be scoped to one column or row, scope it by an attribute that identifies that column in
            the fragment — `aria-colindex`, `col-id`, `data-field`, `data-colindex` — or by position, e.g.
            `[aria-colindex="3"] button[ref="eButtonShowMainFilter"]`. Read the actual value out of the
            HTML you were given; never approximate the scope with a text-matching pseudo-class. Return null only when the element cannot be addressed
            by a selector at all (it lives in Shadow DOM, or it appears only after another script runs).
            For a single control (interaction_type "type") return its `selector` the same way,
            next to `script` - it is checked against the page before the script is trusted.

            {apply_button_section}

            # SELECT ELEMENT SUBTYPE (interaction_type = select_dropdown)
            Check for framework-controlled signs (hashed class names like `Component_xY12z`, data-reactroot/
            data-reactid). If present, plain `.value=`/`.click()` is silently ignored — use a native setter
            first, then dispatch:
            ```js
            (function() {
                const select = /* scoped selector */;
                const option = Array.from(select.options).find(o => o.value === 'TARGET_VALUE');
                const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
                setter.call(select, 'TARGET_VALUE');
                select.dispatchEvent(new Event('change', { bubbles: true }));
                option.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
            })();
            ```
            If no framework signs, plain `select.value = 'TARGET_VALUE'` + `dispatchEvent('change')` (single
            dropdown) or `option.click()` (multi-select listbox) is enough.

            # CRITICAL PRIORITY RULE — disabled twins vs real web components
            A custom element (hyphenated tag, e.g. `<ti-search-field>`) often wraps the REAL control in
            Shadow DOM, while a nearby plain `<input>`/`<select>`/`<button>` "twin" (disabled, zero-size,
            internal to a grid library) may share a similar label but is NOT the real control. Disabled/
            hidden state ALWAYS disqualifies an element, no matter how well its label matches — a visible
            custom element with even a loosely-matching placeholder wins (e.g. placeholder "Filter by
            product or part number" is enough evidence for a "Product number" field). Applies to every
            interaction_type, including "type". For click-style, click the custom element directly:
            `document.querySelector("ti-search-field[placeholder='...']").click();` For "type", reach the
            real input via `.shadowRoot.querySelector(...)`, then apply the full native-setter + dispatch
            pattern above.

            # What to avoid
            A plain data-row `<a>` navigating elsewhere even if text matches; any disabled/hidden/zero-size/
            aria-hidden element; a control that clearly belongs to a different field even if positioned
            nearby; a label text node instead of the actual interactive element (unless the label itself
            wraps the input or has a `for` attribute).

            If nothing usable is found, return null for "script", "none" for control_type, false for
            needs_more_depth, empty available_options, explain in "evidence".

            # Response Format
            Return only valid JSON:
            {
            "chosen_occurrence_index": integer or null,
            "script": string or null,
            "selector": string or null,
            "available_options": [{"value": string, "script": string, "selector": string or null}, ...],
            "control_type": "final_control" | "intermediate_step" | "none",
            "needs_more_depth": boolean,
            "interaction_type": "type" | "click_checkbox" | "click_option" | "click_expand" | "select_dropdown" | "unknown",
            "apply_script": string or null,
            "confidence": "high" | "low" | "none",
            "evidence": string
            }
        """) + apply_button_section
        
        history_block = ""
        if scripts:
            history_block = "Already-clicked scripts so far (in order): " + " -> ".join(scripts)

        response = None
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=[
                        f"Target label (the first is the search_term and the others are help phrases): {search_terms}\n\n"
                        f"{history_block}\n\n"
                        f"HTML fragment:\n{combined_windows}"
                    ],
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json"
                    )
                )
                
                metadata = response.usage_metadata
                self.tokens_usage.append({
                    "prompt_tokens": metadata.prompt_token_count,
                    "output_tokens": metadata.candidates_token_count,
                    "total_tokens": metadata.total_token_count
                })
                break

            except errors.ServerError as e:
                wait_time = self._backoff(attempt)

                print(f"DEBUG: ServerError ({e}), retry {attempt}/{max_retries} in {wait_time}s")
                if attempt == max_retries:
                    print("DEBUG: The number of attempts has been exhausted; an empty response is returned.")
                    return None
                
                time.sleep(wait_time)

            except errors.ClientError as e:
                if getattr(e, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(e):
                    wait_time = 60
                    print(f"DEBUG: 429 RESOURCE_EXHAUSTED, retry {attempt}/{max_retries} in {wait_time}s")
                    if attempt == max_retries:
                        return None
                    time.sleep(wait_time)
                    continue
                raise

        result = self._json_object(response.text)

        if result is None:
            return None

        # Preserve an existing apply_script returned by the model.
        # Only inject the previously-known apply_script when the model
        # did not provide one.
        if (not result.get("apply_script")) and apply_script:
            result["apply_script"] = apply_script

        return result

    def resolve_pagination(self, page_probe: dict, html: str , url: str, max_retries: int = RETRY_ATTEMPTS) -> dict:
  
        pagination_search_terms = [
            "Next", "Next Page", "next page", 'aria-label="Next page"', "chevron-right", "pagination-next", 
            "pagination_nav", "pagination__nav", "Page</span>", "Results per page", 'class="pagination"',
            "data-page=", "per page", page_probe["pagination_evidence"]
        ]
        
        pagination_search_terms = [term for term in pagination_search_terms if term]
        html_windows = self._extract_html_windows(html, pagination_search_terms)

        if not html_windows:
            return {"script": None, "confidence": "none", "evidence": "text not found in HTML"}
        
        combined_windows = "\n\n".join(
            f"--- Occurrence {w['occurrence_index']} ---\n{w['fragment']}"
            for w in html_windows
        )

        system_instruction = ("""
            # Role
            You are given several HTML fragments from a product catalog / data grid page
            (each labeled with an occurrence_index), gathered around occurrences of
            pagination-related text or controls (e.g. "Next", a page counter like
            "Page 1 of 8", pagination icons/buttons). You are also given a page_probe
            object with metadata already inferred about this page (e.g.
            total_records_declared, records_per_page_declared, pagination_evidence) -
            use it as context, not as a literal element to search for in the fragments.

            Your job is to find the control that advances to the NEXT page of results,
            and how to detect when there is no next page left (the stop condition).

            # Task
            Review the provided fragments (each labeled with occurrence_index) and determine:

            1. Is there a genuinely interactive element (button, link, role="button", clickable
               icon, etc.) that advances to the next page of results when clicked? This is
               usually labeled "Next", "Next Page", ">", a chevron-right icon, or similar -
               NEVER "Previous"/"Back"/"<" or a specific page number button.
            2. Does this element expose any signal that indicates it's already on the LAST
               page (e.g. a "disabled" attribute, a disabled/aria-disabled class, aria-disabled
               ="true")? If so, report a short script that reads and returns that signal.

            # How to write the scripts
            Instead of a CSS selector, write a short, standalone JavaScript snippet that finds
            and interacts with the element directly. Runs via executeJavascript - `document`
            is available directly.

            - ALWAYS wrap the entire script in an IIFE: (function() { ... })();
            This prevents "Identifier already declared" errors when multiple scripts run
            sequentially in the same page context and happen to reuse variable names.
            - Use document.querySelector/querySelectorAll with real CSS, or text matching where
            labels aren't cleanly attached via attributes:
            [...document.querySelectorAll('SOME_CONTAINER_SELECTOR')].find(el => el.textContent.trim() === 'Next')
            - NEVER use jQuery-style pseudo-selectors (:contains(), :has-text(), :eq(), :has()) -
            these are INVALID CSS and will throw "is not a valid selector" at runtime, even
            though they look like they should work. Use .textContent.includes(...) + .closest()
            instead. Example - finding a filter icon inside the header cell matching a column
            name (WRONG vs RIGHT):

            WRONG (throws SyntaxError):
            document.querySelector('th:has(div:contains("Part Number")) img[alt="filter"]').click();

            RIGHT:
            (function() {
                const th = [...document.querySelectorAll('th')].find(el => el.textContent.includes('Part Number'));
                const icon = th.querySelector('img[alt="filter"]');
                icon.click();
            })();
            - Shadow DOM (custom tags with a hyphen, e.g. <ti-button>): you MUST use
            .shadowRoot.querySelector(...) explicitly - a plain descendant selector never
            reaches inside a shadow root.
            - next_button_script's final statement must perform the click (.click()), not
            just locate the element.
            - disabled_check_script (if applicable) must be an expression/IIFE that RETURNS
            a boolean (true if currently disabled/on last page, false otherwise) - it does
            NOT click anything. Example:
            (function() {
                const btn = /* same selector logic as next_button_script */;
                return !!(btn && (btn.disabled || btn.classList.contains('disabled') || btn.getAttribute('aria-disabled') === 'true'));
            })();

            # What to avoid
            - "Previous"/"Back" controls, or numbered page buttons (e.g. a button literally
              labeled "3") - only the control that moves FORWARD by one page.
            - A control that navigates to an entirely different page/URL outside the current
              catalog view (e.g. a breadcrumb link).
            - Treating a disabled/hidden element as clickable - if the ONLY next-page control
              you see is already disabled in these fragments, that's a valid finding (it means
              you're on the last page already) - report it via disabled_check_script returning
              true, and still return the next_button_script (script is still valid syntax, it
              just shouldn't be invoked while the disabled state holds).

            # CRITICAL - only ONE control at a time
            Grids sometimes show multiple pagination controls (e.g. one at the top of the
            table, one at the bottom). If more than one qualifying "Next" control is visible
            across fragments, pick ONE consistently (prefer the one closest to the actual data
            rows / bottom of the table if that's discernible from the fragment, otherwise any
            one is fine) - do not return multiple scripts.

            If no next-page control is found in these fragments at all, return null for
            "next_button_script", "none" for "confidence", and explain why in "evidence".

            # Response Format
            Return only valid JSON:
            {
            "next_button_script": string or null,
            "disabled_check_script": string or null,
            "confidence": "high" | "low" | "none",
            "evidence": string
            }
        """)

        response = None
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=f"URL of the page: {url}, HTML fragments: {combined_windows}, page sample to check type of pagination: {page_probe}",
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json"
                    )
                )

                metadata = response.usage_metadata
                self.tokens_usage.append({
                    "prompt_tokens": metadata.prompt_token_count,
                    "output_tokens": metadata.candidates_token_count,
                    "total_tokens": metadata.total_token_count
                })
                break

            except errors.ServerError as e:
                wait_time = self._backoff(attempt)

                print(f"DEBUG: ServerError ({e}), retry {attempt}/{max_retries} in {wait_time}s")
                if attempt == max_retries:
                    print("DEBUG: The number of attempts has been exhausted; an empty response is returned.")
                    return {}
                
                time.sleep(wait_time)

            except errors.ClientError as e:
                if getattr(e, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(e):
                    wait_time = 60
                    print(f"DEBUG: 429 RESOURCE_EXHAUSTED, retry {attempt}/{max_retries} in {wait_time}s")
                    if attempt == max_retries:
                        return None
                    time.sleep(wait_time)
                    continue
                raise

        return self._json_object(response.text)