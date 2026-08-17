import math
from pydoc import html
from unittest import result

from google import genai
from google.genai import types
from google.genai import errors
import os
import re
from difflib import SequenceMatcher
import json
from bs4 import BeautifulSoup
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from markdownify import markdownify

class AIExtractor:
    def __init__(self, api_key: str | None = None):
        self.client = genai.Client(api_key=api_key or os.getenv("GEMINI_API_KEY"))
        self.tokens_usage: list[dict] = []
        self.model: str = "gemini-3.1-flash-lite"
        # DEBUG INFO: use model="gemini-3.1-flash-lite"/"gemini-2.5-flash"/"gemini-2.5-flash-lite" 
        # after "gemini-flash-latest" is burnt out of tokens

    def _extract_html_windows(self, html: str, search_term: str, window_size: int = 3000, word_count: int = 3) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")

        for trash_tag in soup(["script", "style"]):
            trash_tag.decompose()

        clean_html = soup.prettify()

        short_term = " ".join(search_term.split()[:word_count])

        windows = []
        start_idx = 0

        while len(clean_html) > start_idx:
            idx = clean_html.find(short_term, start_idx)
            if idx == -1:
                break

            start = max(0, idx - window_size)
            end = min(len(clean_html), idx + len(short_term) + window_size)

            windows.append({"occurrence_index": len(windows), "fragment": clean_html[start:end]})

            start_idx = idx + len(short_term)

        return windows

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

    def _detect_total_records(self, markdown_content: str, max_retries: int = 2) -> int | None:
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
                    contents=[f"Content:\n{markdown_content[:3000]}"],  # tylko początek, licznik zwykle na górze
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
                time.sleep(2)
        return None

    def _single_extract(self, content: dict, user_prompt: str, max_retries: int = 3):

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

            # Task
            Given the user's field request and the page content described above, extract the
            requested fields for matching records, using the field list declared elsewhere on the
            page (if present) only as a HINT for what fields exist and their expected format -
            never as a guarantee of value order within a detail block.

            Find ALL matching records - do not stop after the first match, and do not stop early
            even if you also need to report pagination info below. Record extraction is the
            PRIMARY task and must be fully exhaustive - go through the entire content from top to
            bottom and extract every single matching record before moving on to anything else,
            using keys matching the user's requested field names. If a field cannot be
            confidently determined for a record, set it to null - never guess or fabricate a value.

            # Pagination detection
            AFTER you have finished extracting every record above, separately check if the page
            shows a page counter (e.g. "Page 1 of 3", "1-50 of 128") anywhere in the content. This
            is a SECONDARY, independent task - it must never cause you to skip, truncate, or stop
            extracting records early. If a counter is found, report the total number of pages as
            "filtered_page_count" (an integer). If no such counter is visible, set
            "filtered_page_count" to null - do not guess.

            # Response Format
            Return only valid JSON - a single JSON object, no explanations, no Markdown
            formatting, no extra text:
            {
            "records": [ ... array of record objects as described above ... ],
            "filtered_page_count": integer or null
            }
            If no records match, "records" must be [].
        """)

        # it is told that free gemini api version is more susceptible for server errors
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=[f"Searching for: {user_prompt}\n\nContent:\n{markdown_content}"],
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
                wait = 2 ** attempt  # 2s, 4s, 8s...
                print(f"DEBUG: ServerError ({e}), retry {attempt}/{max_retries} za {wait}s")

                if attempt == max_retries:
                    print("DEBUG: wyczerpano próby, zwracam pustą listę dla tego chunka")
                    return []
                
                time.sleep(wait)

            except errors.ClientError as e:
                if getattr(e, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(e):
                    wait = 60
                    print(f"DEBUG: 429 RESOURCE_EXHAUSTED, retry {attempt}/{max_retries} w {wait}s")
                    if attempt == max_retries:
                        return {"records": [], "filtered_page_count": None}
                    time.sleep(wait)
                    continue
                raise
            
        try:
            parsed = json.loads(response.text)
        except json.JSONDecodeError:
            if attempt == max_retries:
                print(f"Nie udane próby: {max_retries} dla parsowania jsona")
                return {"records": [], "filtered_page_count": None, "total_records_declared": None}

        if not isinstance(parsed, dict) or "records" not in parsed:
            print(f"DEBUG: model zwrócił zły format (próba {attempt}/{max_retries}), oczekiwano dict z 'records', dostano: {type(parsed)}")

            if attempt == max_retries:
                print("DEBUG: wyczerpano próby, zwracam pusty wynik")

                return {"records": [], "filtered_page_count": None, "total_records_declared": None}

        return parsed
        
    def extract(self, filtered_data: dict, user_prompt: str) -> dict:

        result = self._single_extract(filtered_data, user_prompt)
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
            "page_count": page_count   
        }

    def suggestion(self, content: str, max_retries: int = 3):

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
                wait_time = 2 + 2 ** attempt

                print(f"DEBUG: ServerError ({e}), retry {attempt}/{max_retries} in {wait_time}s")
                if attempt == max_retries:
                    print("DEBUG: The number of attempts has been exhausted; an empty response is returned.")
                    return {}
                
                time.sleep(wait_time)

        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            print(f"DEBUG: failed to parse the response: {response.text[:300]}")
            return {}
        



    def analize_html(self, html: str, search_term: str, max_retries: int = 3):
        html_windows = self._extract_html_windows(html, search_term)

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
        print(len(combined_windows))
        if len(combined_windows) > 150000:
            raise ValueError("DEBUG: MAYDAY we got a problem with length")
        
        system_instruction = ("""
            # Role
            You are given several HTML fragments, each showing a different occurrence of the
            same target label somewhere on a page. Your job is to identify the FULL interaction
            path needed to filter the page's data by this label - not just a single element.

            # Key concept: gate vs target
            Some filter controls are directly interactable (a visible checkbox/input right there
            on the page). Others are hidden behind a GATE - a button/menu trigger (e.g. "Open
            Filter Menu", "Filters" button, a column header's filter icon) that must be clicked
            FIRST to reveal a panel containing the actual field-specific control.

            A fragment either IS the actual interactable control for THIS specific field (its
            label/name/aria-label/nearby text matches the target field), or it's a GENERIC gate
            shared across many unrelated fields (e.g. "Open Filter Menu", a funnel icon) - not
            specific to the target field. If you only see a generic gate in the fragments (the
            field-specific control only appears after clicking it), report that explicitly -
            never treat the gate itself as if it were the field's filter control.

            Real field-specific controls: checkbox, toggle, radio, dropdown option, text input
            whose label/name/aria-label/nearby text matches the target field.
            NOT a control: a plain <a> link inside a data row navigating elsewhere; a generic
            gate/menu-opener not specific to this field (report separately); purely descriptive
            unrelated text.

            # Task
            Review all provided fragments (each labeled with its occurrence_index). For the TARGET
            field, determine:

            1. Is there a fragment showing the field's OWN interactable control? If yes, that's
            your target_script.
            2. Does reaching that control require first clicking a gate (a menu/panel opener)?
            If a gate fragment is visible, report its script separately in "gate_script".
            If the field's control is already directly visible/interactable with no gate
            needed, set "gate_script" to null.
            3. What TYPE of interaction does the target control need once reachable?
            - "type": a text input where you type a value
            - "click_checkbox": a checkbox/toggle to click for a specific value
            - "click_option": a selectable list item/radio for a specific value
            - "select_dropdown": a <select> or dropdown-style element
            - "unknown": control type unclear from the fragment

            # How to write the scripts
            Instead of a CSS selector, write a short, standalone JavaScript snippet that finds
            and interacts with the element directly. This snippet will be executed via a
            browser's executeJavascript action - it runs in the actual page context, so
            `document` is available directly.

            Use whatever combination of these is needed to reliably identify the element:
            - document.querySelector / querySelectorAll with real CSS (id, class, attributes)
            - Text matching where labels aren't cleanly attached via attributes, e.g.:
            [...document.querySelectorAll('SOME_CONTAINER_SELECTOR')]
                .find(el => el.textContent.includes('TARGET TEXT'))
            - .closest(...) to walk up from a matched text node to its actual clickable ancestor
            - For shadow DOM elements (custom tags with a hyphen in the name, e.g.
            <ti-search-field>), access shadow content explicitly via
            `.shadowRoot.querySelector(...)` - never assume a plain descendant CSS selector
            can reach inside a shadow root.

            The snippet's final statement must actually perform the interaction (e.g. end with
            `.click()` or `.focus()`), not just locate the element.

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

            For "type" interactions specifically: the script should ONLY focus or click the
            input element to activate it (e.g. `document.querySelector('...').focus();`).
            Do NOT attempt to set .value or simulate typing in the script - actual text entry
            is handled by a separate step after this script runs.

            # Scoping when elements repeat identically across columns
            Watch for elements carrying BOTH the generic header class AND a more specific role-indicating class 
            (e.g. ag-header-cell + ag-floating-filter) sharing the same positional index — prefer 
            scoping through the MORE SPECIFIC class, since a generic class may match multiple 
            distinct elements (e.g. both the column title header and its floating filter row) 
            at the same index.

            Grid-like tables (e.g. AG Grid and similar libraries) commonly render the SAME
            generic control once per column, with IDENTICAL attributes on every copy - e.g.
            every column gets its own "Open Filter Menu" button, all sharing the same
            aria-label, same internal ref, same class, and often no distinguishing col-id at
            all. A script like document.querySelector('button[aria-label="Open Filter Menu"]')
            is NOT sufficient in this situation - querySelector always returns the FIRST match
            in the whole page, which is very likely the WRONG column's control.

            Such grids often have two separate structures that are siblings, not parent-child:
            one row/section for column headers (with the field names), and another separate
            row/section for the per-column controls (e.g. a "floating filter" row) - linked by
            a shared positional attribute on both sides, such as a matching aria-colindex (or
            similar index/position attribute) on the header cell and on the corresponding
            control cell. If you observe this pattern (repeated identical controls, headers
            and controls in separate structures), you MUST scope the script using that shared
            attribute rather than writing an unscoped/global selector for the repeated control.
            General approach:
            1. Find the header element containing the target field's text.
            2. Read its positional attribute (e.g. aria-colindex).
            3. Find the sibling control-row element that shares the same positional attribute
               value, and query the actual button/input WITHIN that specific scoped element.

            This scoping concern applies to the GATE (the repeated generic control), not
            necessarily to what appears after the gate is clicked - a panel/popup revealed by
            a gate is often a singleton (only one instance exists in the DOM at a time, since
            a user can only open one column's panel at once), so the control(s) inside it can
            usually be queried directly (or scoped by their own literal, unique attributes like
            id/placeholder) without needing the same positional-index scoping.

            If you do NOT see evidence of this repeated-identical-control pattern (e.g. the
            gate or control already has a unique, literal distinguishing attribute of its own),
            a direct unscoped selector is fine - don't add unnecessary complexity when it isn't
            needed.

            # MULTIPLE OPTIONS MODE - important for category-type fields
            If "interaction_type" is "click_checkbox", "click_option", or "select_dropdown"
            (i.e. the field works by picking ONE value out of SEVERAL selectable options,
            rather than typing free text), do NOT just pick and return a single target_script.
            Instead:

            - Find ALL individual selectable options for this field visible in the fragments
            (e.g. every checkbox in the group, every <option> in a dropdown, every radio
            in the set) - not just one example.
            - For each option, report its human-readable value/label (the text or attribute
            that identifies WHICH choice this is, e.g. "ACTIVE", "PREVIEW", a subcategory
            name) and a JS snippet (following the same rules above) that clicks/selects
            that SPECIFIC option only - never a shared template with a placeholder.
            - Populate "available_options" as a list of {"value": ..., "script": ...}
            pairs, one per option found. Leave "target_script" null in this case -
            "available_options" is the source of truth instead.
            - If only a partial set of options is visible in these fragments (e.g. the panel
            might have more options below a scroll boundary not captured here), still
            report what you see, and note this limitation in "evidence".

            # SELECT ELEMENT SUBTYPE
            When "interaction_type" is "select_dropdown", check the <select>'s own attributes
            (applies whether it ends up as a single target_script or as per-option scripts
            under MULTIPLE OPTIONS MODE):

            - SINGLE DROPDOWN (no "multiple" attribute): check for framework-controlled signs
            (CSS-module-style hashed class names like "Component_name__Xy12z", data-reactroot,
            data-reactid, or similar generated/obfuscated class naming on the select or its
            ancestors). If present, plain `.value = X` / `.selected = true` is silently ignored
            by the framework's internal value tracking. Use this pattern instead:

            (function() {
                const select = /* scoped selector, see Scoping section */;
                const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
                setter.call(select, 'TARGET_VALUE');
                select.dispatchEvent(new Event('input', { bubbles: true }));
                select.dispatchEvent(new Event('change', { bubbles: true }));
            })();

            If no framework signs are present, plain `.value = 'TARGET_VALUE'` +
            dispatchEvent('change') is fine.

            - MULTI-SELECT LISTBOX (has "multiple" attribute, often "size" > 1): renders as an
            always-visible scrollable list, not a collapsed dropdown.

            Check for the same framework-controlled signs as above. IF present, plain
            `option.click()` is silently ignored by the framework's internal value tracking
            even though the DOM briefly appears to update. Use this combined pattern instead -
            a native setter "primes" the framework's tracker, THEN a synthetic click on the
            option actually registers:

            (function() {
                const select = /* scoped selector, see Scoping section */;
                const option = Array.from(select.options).find(o => o.value === 'TARGET_VALUE');
                const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
                setter.call(select, 'TARGET_VALUE');
                select.dispatchEvent(new Event('change', { bubbles: true }));
                option.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
            })();

            IF no framework signs are present, plain click is sufficient:

            (function() {
                const select = /* scoped selector, see Scoping section */;
                const option = Array.from(select.options).find(o => o.value === 'TARGET_VALUE');
                option.click();
            })();

            # CRITICAL PRIORITY RULE
            Web components (hyphenated tags, e.g. <ti-search-field>) often wrap the real
            functional input in Shadow DOM, while a nearby plain <input>/<select>/<button>
            "twin" (disabled, zero-size, or otherwise internal) may share a similar aria-label
            but is NOT the real control.

            (This rule applies regardless of interaction_type — including 'type' fields. 
            A disabled <input> is never a valid target_script, full stop.)

            Rule: disabled/hidden state always disqualifies an element, regardless of how well
            its label matches. A visible custom element with even a loosely-matching
            placeholder/attribute is always preferred (e.g. "Filter by product or part number"
            is sufficient evidence for a "Product number" field). This applies even if the
            disabled twin is the ONLY thing currently visible - in that case return null for
            target_script and set gate_needed=true instead, rather than selecting the disabled
            element as a fallback. A gate button being present doesn't change this either.

            Always wrap the entire generated script in an IIFE: (function() { ... })();
            This prevents "Identifier already declared" errors when multiple scripts execute
            sequentially in the same page context and reuse variable names like 'header' or
            'select'.

            Script pattern: for click-style interactions where clicking the outer custom
            element itself is enough, use it directly:
            document.querySelector("ti-search-field[placeholder='...']").click();
            For "type"/"focus" interactions you MUST go through shadow DOM - do NOT call
            .focus() directly on the custom element's outer tag:
            document.querySelector("ti-search-field[placeholder='...']").shadowRoot.querySelector('input').focus();

            If NONE of the fragments look like a real filter control (field-specific or gate),
            return null for both scripts, empty list for "available_options", and explain why
            in "evidence".

            # Response Format
            Return only valid JSON:
            {
            "chosen_occurrence_index": integer or null,
            "gate_script": string or null,
            "gate_needed": boolean,
            "target_script": string or null,
            "available_options": [{"value": string, "script": string}, ...],
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
                    contents=[f"Target label: {search_term}\n\nHTML fragment:\n{combined_windows}"],
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
                wait_time = 2 + 2 ** attempt

                print(f"DEBUG: ServerError ({e}), retry {attempt}/{max_retries} in {wait_time}s")
                if attempt == max_retries:
                    print("DEBUG: The number of attempts has been exhausted; an empty response is returned.")
                    return {}
                
                time.sleep(wait_time)

        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            print(f"DEBUG: failed to parse the response: {response.text[:300]}")
            return {}
        
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
    
    def find_script(self, scripts: list, filter: dict, html: str, max_retries: int = 3) -> dict:
        """we take the filter["name"] so llm will know what its actually looking for"""

        search_terms = filter.get("search_terms") or [filter["name"]]
        html_windows = []

        for term in search_terms:
            windows = self._extract_html_windows(html, term)
            if windows:
                html_windows.extend(windows)

        if not html_windows:
            return {"script": None, "confidence": "none", "evidence": "text not found in HTML"}
        
        combined_windows = "\n\n".join(
            f"--- Occurrence {w['occurrence_index']} ---\n{w['fragment']}"
            for w in html_windows
        )
        """ 
        # FOR DEBUG
        import time
        with open(f"data/test_files/combined_windows/debug_windows_{time.time()}.txt", "w", encoding="utf-8") as f:
            f.write(combined_windows)
        """

        system_instruction = ("""
            # Role
            You are given HTML fragments showing occurrences of a target field name, found
            inside a page AFTER a sequence of clicks has already been performed to open some
            filter panel/menu. Your job is to determine the NEXT single interaction step needed
            to reach an actual, usable filter control for this field - and whether that step
            is already the final control, or just another intermediate step deeper into a
            nested menu.

            # Context
            This is one step in a multi-click discovery process. Earlier clicks already opened
            a panel (e.g. a generic "Filters" menu). Now you're looking at what that panel
            revealed, specifically around occurrences of the target field's name. Sometimes
            a single field name inside a filter panel is itself an intermediate control - e.g.
            clicking the field name row/label opens a SUB-panel with the field's actual options
            (checkboxes, a search input, a range slider). Other times, the field name occurrence
            already IS or directly precedes the final interactive control.

            # Task
            Review the provided fragments (each labeled with occurrence_index) and determine:

            1. Is there a genuinely interactive element (input, checkbox, radio, button, role=
            "option"/"button"/"checkbox", clickable label, dropdown trigger, etc.) associated
            with the target field's name in any fragment? If multiple candidates exist, pick
            the one most clearly tied to the target field specifically (not a generic sibling
            control for a different field that happens to be nearby).

            2. Classify what that element does:
            - "final_control": interacting with it directly narrows results by this field
                (e.g. a text input to type a value, a checkbox for one specific value, a
                dropdown/select already showing this field's values)
            - "intermediate_step": interacting with it reveals a FURTHER sub-panel/submenu
                specific to this field, which itself will contain the final control
            - "none": no interactive element found for this field in these fragments

            3. Based on the classification, set "needs_more_depth":
            - true only for "intermediate_step"
            - false for "final_control" or "none"

            4. What TYPE of interaction does the chosen element need?
            - "type": a text input where you type a value
            - "click_checkbox": a checkbox/toggle for a specific value
            - "click_option": a selectable list item/radio for a specific value
            - "click_expand": clicking to reveal a sub-panel (only valid when needs_more_depth is true)
            - "select_dropdown": a <select> element
            - "unknown": type unclear from the fragment

            # General script rules
            Instead of a CSS selector, write a short, standalone JavaScript snippet that finds
            and interacts with the chosen element directly. Runs via executeJavascript - `document`
            is available directly.

            - ALWAYS wrap the entire script in an IIFE: (function() { ... })();
            This prevents "Identifier already declared" errors when multiple scripts run
            sequentially in the same page context and happen to reuse variable names.
            - Use document.querySelector/querySelectorAll with real CSS, or text matching where
            labels aren't cleanly attached via attributes:
            [...document.querySelectorAll('SOME_CONTAINER_SELECTOR')].find(el => el.textContent.includes('TARGET TEXT'))
            - .closest(...) to walk up from a matched text node to its actual clickable ancestor.
            - Shadow DOM (custom tags with a hyphen, e.g. <ti-search-field>): you MUST use
            .shadowRoot.querySelector(...) explicitly. A plain descendant selector like
            `ti-search-field input` NEVER works. Example:
            document.querySelector('ti-search-field[placeholder="..."]').shadowRoot.querySelector('input').focus();
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
            - The script's final statement must perform the interaction (.click()/.focus()), not
            just locate the element.
            - For "type" interactions: ONLY focus/click the input to activate it
            (document.querySelector('...').focus();). Do NOT set .value or simulate typing -
            text entry is handled by a separate step after this script runs.

            # Scoping - repeated identical controls across columns
            Watch for elements carrying BOTH the generic header class AND a more specific role-indicating class 
            (e.g. ag-header-cell + ag-floating-filter) sharing the same positional index — prefer 
            scoping through the MORE SPECIFIC class, since a generic class may match multiple 
            distinct elements (e.g. both the column title header and its floating filter row) 
            at the same index.
            
            Grid-like tables (AG Grid and similar) commonly render the SAME generic control once
            per column with IDENTICAL attributes on every copy (same class, same aria-label, no
            distinguishing col-id). An unscoped selector like
            document.querySelector('button[aria-label="Open Filter Menu"]') or
            document.querySelectorAll('select.SomeSharedClass')[N]
            is NOT reliable - it returns the FIRST/Nth match in the WHOLE page, which is very
            likely the WRONG column.

            This applies to BOTH the gate script AND any target/option script that queries a
            repeated shared class (e.g. a filter <select> whose class is reused by every column).
            Never guess a global index. Instead, scope through the same header element used to
            find the field, on every script that touches a repeated-class element:

            (function() {
            const header = [...document.querySelectorAll('th')].find(th => th.textContent.includes('TARGET FIELD NAME'));
            const select = header.querySelector('select.SOME_REPEATED_CLASS');
            const option = Array.from(select.options).find(o => o.value === 'TARGET_VALUE');
            option.click();
            })();

            Grids sometimes split header and controls into separate sibling structures (not
            parent-child), linked by a shared positional attribute like aria-colindex on both
            sides. If you observe that pattern instead, scope via the matching aria-colindex
            value rather than a parent/child relationship.

            If the control instead appears INSIDE an already-opened, singleton panel (only one
            instance can be open at a time) and has its own unique literal attribute (id,
            placeholder), a direct unscoped selector using that unique attribute is fine - don't
            add unnecessary scoping complexity when the element is already uniquely identifiable
            on its own.

            # MULTIPLE OPTIONS MODE - category-type fields
            If "control_type" is "final_control" AND "interaction_type" is "click_checkbox",
            "click_option", or "select_dropdown", do NOT return a single script. Instead:
            - Find ALL individual selectable options visible in the fragments.
            - For each, report its value/label and a JS snippet (following all rules above,
            including scoping) that selects that SPECIFIC option only - never a shared
            template with a placeholder.
            - Populate "available_options" as [{"value": ..., "script": ...}]. Leave top-level
            "script" null.
            - If only a partial option set is visible, report what you see and note it in "evidence".

            For "type" interactions, ignore this section - return a single "script", empty
            "available_options".

            # SELECT ELEMENT SUBTYPE
            When "interaction_type" is "select_dropdown", check the <select>'s own attributes:

            - SINGLE DROPDOWN (no "multiple" attribute): if the surrounding markup shows signs
            of a framework-controlled component (CSS-module-style hashed class names like
            "Component_name__Xy12z", data-reactroot, data-reactid, or similar generated/
            obfuscated class naming), plain `.value = X` / `.selected = true` is silently
            ignored by the framework. Use this pattern instead:

            (function() {
                const select = /* scoped selector, see Scoping section */;
                const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
                setter.call(select, 'TARGET_VALUE');
                select.dispatchEvent(new Event('input', { bubbles: true }));
                select.dispatchEvent(new Event('change', { bubbles: true }));
            })();

            If no framework signs are present, plain `.value = 'TARGET_VALUE'` + dispatchEvent('change') is fine.

            - MULTI-SELECT LISTBOX (has "multiple" attribute, often "size" > 1): renders as an
            always-visible scrollable list, not a collapsed dropdown.

            Check for framework-controlled signs first (CSS-module-style hashed class names
            like "Component_name__Xy12z", data-reactroot, data-reactid, or similar generated/
            obfuscated class naming on the select or its ancestors).

            IF framework signs are present, plain `option.click()` is silently ignored by the
            framework's internal value tracking even though the DOM briefly appears to update.
            Use this combined pattern instead - a native setter "primes" the framework's
            tracker, THEN a synthetic click on the option actually registers:

            (function() {
                const select = /* scoped selector, see Scoping section */;
                const option = Array.from(select.options).find(o => o.value === 'TARGET_VALUE');
                const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
                setter.call(select, 'TARGET_VALUE');
                select.dispatchEvent(new Event('change', { bubbles: true }));
                option.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
            })();

            IF no framework signs are present, plain click is sufficient:

            (function() {
                const select = /* scoped selector, see Scoping section */;
                const option = Array.from(select.options).find(o => o.value === 'TARGET_VALUE');
                option.click();
            })();

            # CRITICAL PRIORITY RULE - shadow DOM web components vs disabled twins
            Web components (custom hyphenated tags, e.g. <ti-search-field>) frequently WRAP a
            real functional input inside Shadow DOM, while a nearby plain <input> "twin" in the
            light DOM (same visual area, similar aria-label) is often DISABLED/invisible/internal
            to an underlying grid library - not the actual control.

            (This rule applies regardless of interaction_type — including 'type' fields. 
            A disabled <input> is never a valid target_script, full stop.)

            When both appear for the same target field:
            - a plain <input>/<select>/<button> that is disabled, zero-size, or otherwise an
            internal implementation detail, AND
            - a custom web component with a visible "placeholder"/user-facing attribute in the
            same area

            You MUST choose the custom web component, even if its aria-label matches less well.
            A visible placeholder referencing the target field (even loosely) is sufficient
            evidence. Disabled state always disqualifies the other element regardless of label match.

            For click-style interactions, click the custom element directly:
            document.querySelector("ti-search-field[placeholder='...']").click();
            For type/focus interactions, go through shadowRoot:
            document.querySelector("ti-search-field[placeholder='...']").shadowRoot.querySelector('input').focus();

            # What to avoid
            - A plain data-row link (<a>) navigating to an unrelated page, even if its text matches.
            - An element with "disabled", or clearly hidden/zero-size/aria-hidden.
            - A control that clearly belongs to a DIFFERENT field, even if positioned nearby.
            - Selecting a label text node instead of the actual interactive element, unless the
            label itself is what's clickable (wraps the input, or has a "for" attribute).

            If nothing usable is found, return null for "script", "none" for "control_type",
            false for "needs_more_depth", empty list for "available_options", explain in "evidence".

            # Response Format
            Return only valid JSON:
            {
            "chosen_occurrence_index": integer or null,
            "script": string or null,
            "available_options": [{"value": string, "script": string}, ...],
            "control_type": "final_control" | "intermediate_step" | "none",
            "needs_more_depth": boolean,
            "interaction_type": "type" | "click_checkbox" | "click_option" | "click_expand" | "select_dropdown" | "unknown",
            "confidence": "high" | "low" | "none",
            "evidence": string
            }
        """)
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
                wait_time = 2 + 2 ** attempt

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

        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            print(f"DEBUG: failed to parse the response: {response.text[:300]}")
            return None

    def resolve_pagination(self, page_probe: dict, html: str , url: str, max_retries: int = 3) -> dict:
        html_windows = []
        pagination_search_terms = ["Next", "Next Page", 'aria-label="Next page"', "chevron-right", "pagination-next", 
                                   "pagination_nav", "pagination__nav", "Page</span>", "Results per page",
                                   page_probe["pagination_evidence"]
                                   ]
        
        pagination_search_terms = [term for term in pagination_search_terms if term]

        for term in pagination_search_terms:
            windows = self._extract_html_windows(html, term)
            if windows:
                html_windows.extend(windows)

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
                wait_time = 2 + 2 ** attempt

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

        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            print(f"DEBUG: failed to parse the response: {response.text[:300]}")
            return None