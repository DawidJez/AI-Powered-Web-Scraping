from google import genai
from google.genai import types
from google.genai import errors
import os
import json
from bs4 import BeautifulSoup
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

class AIExtractor:
    def __init__(self, api_key: str | None = None):
        self.client = genai.Client(api_key=api_key or os.getenv("GEMINI_API_KEY"))
        self.tokens_usage: list[dict] = []
        self.model: str = "gemini-3.1-flash-lite"
        # DEBUG INFO: use model="gemini-3.1-flash-lite"/"gemini-2.5-flash"/"gemini-2.5-flash-lite" 
        # after "gemini-flash-latest" is burnt out of tokens

    def _extract_html_windows(self, html: str, search_term: str, window_size: int = 3000) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")

        for trash_tag in soup(["script", "style"]):
            trash_tag.decompose()

        clean_html = soup.prettify()

        windows = []
        start_idx = 0

        while len(clean_html) > start_idx:
            idx = clean_html.find(search_term, start_idx)
            if idx == -1:
                break
            
            start = max(0, idx - window_size)
            end = min(len(clean_html), idx + len(search_term) + window_size)

            windows.append({"occurrence_index": len(windows), "fragment": clean_html[start:end]})

            start_idx = idx + len(search_term)

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

    def _single_extract(self, content: str, user_prompt: str, max_retries: int = 3):
        start = time.time()
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

            Find ALL matching records - do not stop after the first match.
            Return a JSON array, one object per record, with keys matching the user's requested
            field names. If a field cannot be confidently determined for a record, set it to null -
            never guess or fabricate a value.

            # Response Format
            Return only valid JSON - a JSON array. No explanations, no Markdown formatting, no extra text.
            If nothing matches, return [].
        """)

        # it is told that free gemini api version is more susceptible for server errors
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=[f"Searching for: {user_prompt}\n\nContent:\n{content}"],
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json"
                    )
                )
                usage = response.usage_metadata
                # debug debug
                print(f"Prompt tokens : {usage.prompt_token_count}")
                print(f"Output tokens : {usage.candidates_token_count}")
                print(f"Total tokens  : {usage.total_token_count}")
                break  # ssuccess
            
            except errors.ServerError as e:
                wait = 2 ** attempt  # 2s, 4s, 8s...
                print(f"DEBUG: ServerError ({e}), retry {attempt}/{max_retries} za {wait}s")

                if attempt == max_retries:
                    print("DEBUG: wyczerpano próby, zwracam pustą listę dla tego chunka")
                    return []
                
                time.sleep(wait)

        end = time.time()
        print(f"DEBUG: extraction time: {end - start}")
            
        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            print(f"DEBUG: nie udało się sparsować odpowiedzi: {response.text[:300]}")
            return []
        
    def extract(self, pages: list[str], user_prompt: str, chunk_size: int = 150_000, search_term: str | None = None) -> list:
        """Testuje pozbycie się chunkowania, w markdown potrzebne jest analizaowanie przynajmniej pełnej strony, 
        w chunkowaniu podział mógły zepsuć relacje między rekordami, np. compact entry i detailed block 
        mogłyby znaleźć się w różnych chunkach i ai miałoby problem z dopasowaniem ich do siebie"""
        
        # content = self._clean_html(content)
        # print(f"DEBUG: cleaned content length = {len(content)}")

        """
        if len(content) < chunk_size:
            return self._single_extract(content, user_prompt)
        
        chunks = self._chunk_segmentation(content, chunk_size)
        print(f"DEBUG: split into {len(chunks)} chunks")
        """
        if search_term:
            pages = [c for c in  pages if search_term in c] # pre-filter
            print(f"DEBUG: after pre-filter by '{search_term}': {len(pages)} relevant pages")

        all_records = []
        batch_size = 5
        
        for start in range(0, len(pages), batch_size):
            batch = pages[start:start + batch_size]

            with ThreadPoolExecutor(min(len(batch), 5)) as thread_executor:
                threads = {
                    thread_executor.submit(self._single_extract, page, user_prompt): i
                    for i, page in enumerate(batch)
                }

                completed = 0
                for thread in as_completed(threads):
                    completed += 1
                    page_index = threads[thread]
                    print(f"DEBUG: page {page_index + 1}/{len(pages)} done ({completed}/{len(pages)} total)")

                    all_records.extend(thread.result())

            if start + batch_size < len(pages):
                print("DEBUG: czekam 60s przed kolejną paczką (limit darmowego API)")
                time.sleep(60)

        return self._deduplicate(all_records)
    


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
            return {"selector": None, "confidence": "none", "evidence": "text not found in HTML"}
        
        combined_windows = "\n\n".join(
            f"--- Occurrence {w['occurrence_index']} ---\n{w['fragment']}"
            for w in html_windows
        )
        
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

            Distinguish between:
            - A fragment that IS the actual interactable control for THIS specific field
            (e.g. a checkbox/input whose label or nearby text matches the target field name)
            - A fragment that is a GENERIC gate opening a panel with MANY unrelated fields inside
            (e.g. "Open Filter Menu", "Filters", a hamburger/funnel icon) - this is not
            specific to the target field, it's shared infrastructure

            If you only see a generic gate in the fragments (no fragment shows the field-specific
            control itself, e.g. because it only appears after the gate is clicked), report that
            explicitly - do not treat the gate itself as if it were the field's filter control.

            # What counts as a real field-specific filter control
            Typically a checkbox, toggle, radio button, dropdown option, or text input whose
            label/name/aria-label/nearby text matches the target field - meant to narrow results
            specifically by that field's value.

            # What does NOT count
            - A plain link (<a>) inside a data row/cell navigating to a different page
            - A generic gate/menu-opener not specific to this field (see above - report separately)
            - Purely descriptive text unrelated to filtering

            # Task
            Review all provided fragments (each labeled with its occurrence_index). For the TARGET
            field, determine:

            1. Is there a fragment showing the field's OWN interactable control? If yes, that's
            your target_selector.
            2. Does reaching that control require first clicking a gate (a menu/panel opener)?
            If a gate fragment is visible, report its selector separately in "gate_selector".
            If the field's control is already directly visible/interactable with no gate
            needed, set "gate_selector" to null.
            3. What TYPE of interaction does the target control need once reachable?
            - "type": a text input where you type a value
            - "click_checkbox": a checkbox/toggle to click for a specific value
            - "click_option": a selectable list item/radio for a specific value
            - "select_dropdown": a <select> or dropdown-style element
            - "unknown": control type unclear from the fragment

            Build CSS selectors using ONLY attributes LITERALLY present in the fragments (id,
            class, name, aria-label, data-* attributes, role). Never invent attributes not
            visibly present.

            CRITICAL: Never select an element that has a "disabled" attribute, or that appears
            to be a hidden/internal implementation detail (e.g. zero-size, aria-hidden, or part
            of a data-grid's internal filter row that isn't the page's actual visible search
            control). If the fragment shows both a disabled/hidden input AND a separate visible
            custom element (e.g. a custom tag like <ti-search-field>, or any element with a
            visible "placeholder" attribute suggesting it's meant for user typing), prefer the
            visible custom element instead. Explain this reasoning in "evidence" if relevant.

            If NONE of the fragments look like a real filter control (field-specific or gate),
            return null for both selectors and explain why in "evidence".

            # Response Format
            Return only valid JSON:
            {
            "chosen_occurrence_index": integer or null,
            "gate_selector": string or null,
            "gate_needed": boolean,
            "target_selector": string or null,
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
    
    def find_selector(self, selectors: list, filter: dict, html: str, max_retries: int = 3) -> dict:
        """we take the filter["name"] so llm will know what its actually looking for"""

        search_term = filter["name"]
        html_windows = self._extract_html_windows(html, search_term)

        if not html_windows:
            return {"selector": None, "confidence": "none", "evidence": "text not found in HTML"}
        
        combined_windows = "\n\n".join(
            f"--- Occurrence {w['occurrence_index']} ---\n{w['fragment']}"
            for w in html_windows
        )

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
                specific to this field, which itself will contain the final control (e.g.
                clicking the field's row/label expands an accordion, opens a flyout, or
                navigates one level deeper before any actual filter value can be set)
            - "none": no interactive element found for this field in these fragments -
                only descriptive/non-interactive text, or the field is not present here at all

            3. Based on the classification, set "needs_more_depth":
            - true only for "intermediate_step" - meaning: after this click, another
                iteration of this same discovery process should run again on the newly
                revealed content to find the field's actual final control
            - false for "final_control" (we're done, this is directly usable) or "none"
                (nothing found, retrying deeper here would not help)

            4. What TYPE of interaction does the chosen element need?
            - "type": a text input where you type a value
            - "click_checkbox": a checkbox/toggle for a specific value
            - "click_option": a selectable list item/radio for a specific value
            - "click_expand": clicking to reveal a sub-panel (only valid when
                needs_more_depth is true)
            - "select_dropdown": a <select> or dropdown-style element
            - "unknown": type unclear from the fragment

            Build the CSS selector using ONLY attributes LITERALLY present in the fragment
            (id, class, name, aria-label, data-* attributes, role). Never invent attributes
            not visibly present in the HTML.

            # What to avoid
            - Do not select a plain data-row link (<a>) that navigates to an unrelated page
            (e.g. a product page), even if its text happens to match the target field name.
            - Do not select an element with a "disabled" attribute, or one that is clearly
            hidden/zero-size/aria-hidden.
            - Do not select a control that clearly belongs to a DIFFERENT field, even if it's
            positioned near the target field's occurrence.
            - If the same target text appears both as a plain label AND as part of an
            interactive element (e.g. a checkbox with an associated <label>), select the
            actual interactive element (input/checkbox/role) itself, not just the label
            text node, unless the label is what's clickable (e.g. wraps the input or has
            a "for" attribute pointing to it - then the label itself is a valid choice).

            If nothing usable is found across all fragments, return null for "selector",
            "none" for "control_type", false for "needs_more_depth", and explain in "evidence".

            # Response Format
            Return only valid JSON:
            {
            "chosen_occurrence_index": integer or null,
            "selector": string or null,
            "control_type": "final_control" | "intermediate_step" | "none",
            "needs_more_depth": boolean,
            "interaction_type": "type" | "click_checkbox" | "click_option" | "click_expand" | "select_dropdown" | "unknown",
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
                    return None
                
                time.sleep(wait_time)

        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            print(f"DEBUG: failed to parse the response: {response.text[:300]}")
            return None