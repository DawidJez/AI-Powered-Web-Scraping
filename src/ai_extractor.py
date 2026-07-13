from google import genai
from google.genai import types
from google.genai import errors
import os
import json
from bs4 import BeautifulSoup
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

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
        system_instruction = ( """
            # Role
            You are an assistant specialized in extracting structured data from scraped web pages.
            # Task
            You will receive the raw HTML content of a web page together with a description of the information requested by the user.
            Find **ALL** matching elements or records on the page. Do not stop after the first match and do not omit any valid results.
            Return the result as a JSON array, where each object represents one extracted record. The object keys must match the fields requested by the user.
            If the user requests a link (for example, a PDF, image, or product page), always return the full absolute URL.
            If a requested field cannot be found for a particular record, set its value to `null`. Never guess or invent missing information.
            Extract information only from the provided HTML content.
            # Response Format
            Return **only** valid JSON.
            Do not include explanations, comments, Markdown formatting, or any additional text.
            If no matching records are found, return an empty JSON array:
            []s
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
            
        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            print(f"DEBUG: nie udało się sparsować odpowiedzi: {response.text[:300]}")
            return []
        
    def extract(self, content: str, user_prompt: str, chunk_size: int = 150_000, search_term: str | None = None) -> list:
        content = self._clean_html(content)
        print(f"DEBUG: cleaned content length = {len(content)}")

        if len(content) < chunk_size:
            return self._single_extract(content, user_prompt)
        
        chunks = self._chunk_segmentation(content, chunk_size)
        print(f"DEBUG: split into {len(chunks)} chunks")

        if search_term:
            chunks = [c for c in  chunks if search_term in c] # pre-filter
            print(f"DEBUG: after pre-filter by '{search_term}': {len(chunks)} relevant chunks")

        all_records = []
        
        with ThreadPoolExecutor(min(len(chunks), 5)) as thread_executor:
            threads = {
                thread_executor.submit(self._single_extract, chunk, user_prompt): i
                for i, chunk in enumerate(chunks)
            }

            completed = 0
            for thread in as_completed(threads):
                completed += 1
                chunk_index = threads[thread]
                print(f"DEBUG: chunk {chunk_index + 1}/{len(chunks)} gotowy ({completed}/{len(chunks)} total)")

                all_records.extend(thread.result())

        return self._deduplicate(all_records)