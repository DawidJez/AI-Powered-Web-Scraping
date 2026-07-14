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

    def _clean_html(self, html: str):
        #html cleaning
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["script", "style", "svg", "noscript"]):
            tag.decompose()
        
        return str(soup)
    
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
                    model="gemini-flash-latest",
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