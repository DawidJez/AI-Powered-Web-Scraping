import time

from ai_extractor import AIExtractor
from firecrawl_client import FirecrawlClient


class Orchestrator:
    def __init__(self):
        self.extractor = AIExtractor()
        self.firecrawl = FirecrawlClient()

    def page_sample(self, url: str) -> dict:
        """ Fetchers a single/first page from url and asks AI 
        what is possible to extract from it. Returns a list of field names 
        and analized html content required to filtered scraping. """

        page = self.firecrawl.fetch_page(url)
        result = {
            "page_sample_result": self.extractor.suggestion(page["markdown"]),
            "html_to_analize": page["html"]
        }

        return result
    
    def search_for_filters(self, html: str, search_term: str):
        """ Analizes html content and returns a list of filters found on the page. """
        result = self.extractor.analize_html(html, search_term)

        return result

    def filtered_scraping(self, url: str, analisys: dict, page_sample_result: dict, csv_list: list[str], user_choice: str) -> dict:
       
        selector = analisys["selector"]
        filter_options = page_sample_result.get("filter_options",[])
        available_fields = page_sample_result.get("available_fields", [])

        matched_filter = next((f for f in filter_options if f["name"] == user_choice), None)
        filter = matched_filter

        if matched_filter is None:
            matched_filter = next((f for f in available_fields if f == user_choice), None) # dict -> str

            if matched_filter is None:
                raise ValueError(f"Filter '{user_choice}' not found in detected filter_options")
            
            identifier_hints = ["number", "id", "code", "part", "sku", "mpn", "name"]
            matched_filter_lc = matched_filter.lower()

            if any(hint in matched_filter_lc for hint in identifier_hints):
                filter = {
                    "name": matched_filter,
                    "cardinality": "identifier",
                    "example_values": None
                }
            else:
                filter = {
                    "name": matched_filter,
                    "cardinality": "category",
                    "example_values": None
                }
                
        filtered_data = self.firecrawl.fetch_filtered_data(url, selector, filter, csv_list)

        return filtered_data

    def test(self):
        url = "https://www.ti.com/product-category/sensors/products.html"
        user_prompt = f"""
            You are given a list of MPNs called "csv_list".

            For EACH MPN in csv_list:

            - Find every element on the page whose MPN (product number) starts with,
            or is a variant of, that MPN - including the exact base part as well as
            any suffixed or derivative versions (for example: "-Q1", trailing letters,
            automotive, industrial, military/space variants, or any other product that
            shares the same root/prefix).

            - Do NOT limit the search to an exact match.
            - Include ALL matching variants found on the page.
            - There may be zero, one, or many matches for each requested MPN.
            - Do NOT merge, deduplicate, or discard any occurrence.
            - Return every occurrence as a separate object.
            - Do NOT guess or fabricate missing values.

            For every occurrence extract:

            - Requested MPN (the value from csv_list that produced this match)
            - MPN
            - Product page URL
            - Product image URL
            - Subcategory
            - Rating
            - Package type
            - Pin count
            - TI functional safety category
            - Operating temperature range (°C)
            - Package area (mm²)
            - Package size (L × W) (mm)
            - Price/Quantity (USD)
            - Product status
            - Full URL of the PDF datasheet
            - Full URL of the HTML datasheet

            Return a single JSON array containing all extracted objects.

            If none of the requested MPNs are found, return an empty array [].
        """

        start_test = time.time()
        print("DEBUG: Start testu")

        #with open("data/test_files/test_page_sample.txt", "r", encoding="utf-8") as f:
        #    test_content = f.read()
        
        #result = {
        #    "markdown_result": "",
        #    "html_to_analize": test_content
        #}
        #"""
        start_sample = time.time()
        result = self.page_sample(url)
        end_sample = time.time()
        
        print(f"DEBUG: sample page result = \n{result["page_sample_result"]}")
        user_choice = input("Enter your choice: ").strip()
        #csv_list = ["TMP75","TMP117","TMP275"]
        csv_list = ["TMP117"]

        start_analisys = time.time()
        analisys = self.search_for_filters(result["html_to_analize"], user_choice)
        end_analisys = time.time()
        #"""
        start_filter = time.time()
        filtered_data = self.filtered_scraping(url, analisys, result["page_sample_result"], csv_list, user_choice)
        ai_filtered_data = self.extractor.debug_ask_ai_about_products(filtered_data)
        #selector = "input[aria-label='Product number Filter Input']"
        #filter = {
        #    "name": "Product number",
        #    "cardinality": "identifier",
        #    "example_values": None
        #}
        #csv_list = ["TMP117"]
        #filtered_data = self.firecrawl.fetch_filtered_data(url, selector, filter, csv_list)
        end_filter= time.time()

        end_test = time.time()
        
        prompt_tokens = sum(t["prompt_tokens"] for t in self.extractor.tokens_usage)
        output_tokens = sum(t["output_tokens"] for t in self.extractor.tokens_usage)
        total_tokens = sum(t["total_tokens"] for t in self.extractor.tokens_usage)

        print("\n===== DEBUG: TOKEN USAGE =====")
        print("\n=== GEMINI ===")
        print(f"Prompt tokens : {prompt_tokens:,}")
        print(f"Output tokens : {output_tokens:,}")
        print(f"Total tokens  : {total_tokens:,}")
        
        print(f"DEBUG: test time: {end_test - start_test}")
        print(f"\nDEBUG: suggestion time: {end_sample - start_sample:.2f}s")
        print(f"\nDEBUG: analisys time: {end_analisys - start_analisys:.2f}s")
        print(f"\nDEBUG: filtering data time: {end_filter - start_filter:.2f}s")

        # print(f"\nDEBUG: result = \n{result}")
        print(f"DEBUG: sample page result = \n{result["page_sample_result"]}")
        print(f"DEBUG: analisys result = \n{analisys}")
        print(f"DEBUG: filtered data result = \n{ai_filtered_data}")
        return 1