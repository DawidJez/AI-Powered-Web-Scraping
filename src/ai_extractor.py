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

    def _extract_html_window(self, html: str, search_term: str, window_size: int = 15000) -> str:
        soup = BeautifulSoup(html, "html.parser")

        for trash_tag in soup(["script", "style"]):
            trash_tag.decompose()

        clean_html = soup.prettify()

        idx = clean_html.find(search_term)
        if idx == -1:
            return ""
        
        start = max(0, idx - window_size)
        end = min(len(clean_html), idx + len(search_term) + window_size)

        return clean_html[start:end]
    
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
        start = time.time()

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
            "filter_options": [{"name": string, "example_values": [string, ...] or null}],
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

        end = time.time()
        print(f"\nDEBUG: suggestion time: {end - start:.2f}s")

        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            print(f"DEBUG: failed to parse the response: {response.text[:300]}")
            return {}
        
    def analize_html(self, html: str, search_term: str, max_retries: int = 3):
        start = time.time()
        
        html_window = self._extract_html_window(html, search_term)

        if not html_window:
            return {"selector": None, "confidence": "none", "evidence": "text not found in HTML"}
        
        system_instruction = ("""
            # Role
            You analyze a small HTML fragment to find the clickable element that
            corresponds to a specific visible label/text.

            # Task
            Given the fragment and a target label, identify the actual HTML element
            (or its nearest clickable ancestor - input, button, a, or an element with
            role="button"/"checkbox"/"option") that represents this label.

            Build a CSS selector using ONLY attributes that are LITERALLY present in
            the given fragment (id, class, name, aria-label, data-* attributes, role).
            Prefer, in order of preference: id > data-* attributes > aria-label > a
            unique combination of tag + class.

            Never invent an attribute or value that is not visibly present in the
            fragment. If no attribute is unique/reliable enough to build a confident
            selector, return null for "selector" and explain why in "evidence" -
            do NOT guess a selector just to provide an answer.

            # Response Format
            Return only valid JSON:
            {
            "selector": string or null,
            "confidence": "high" | "low" | "none",
            "evidence": string
            }
        """)

        response = None
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=[f"Content:\n{html_window}"],
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

        end = time.time()
        print(f"\nDEBUG: analize_html time: {end - start:.2f}s")

        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            print(f"DEBUG: failed to parse the response: {response.text[:300]}")
            return {}