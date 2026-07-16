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

        matched_filter = next((f for f in filter_options if f["name"] == user_choice), None)

        if matched_filter is None:
            raise ValueError(f"Filter '{user_choice}' not found in detected filter_options")

        filtered_data = self.firecrawl.fetch_filtered_data(url, selector, matched_filter, csv_list)

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

        start = time.time()

        with open("data/test_files/test_page_sample.txt", "r", encoding="utf-8") as f:
            test_content = f.read()
        
        #result = {
        #    "markdown_result": "",
        #    "html_to_analize": test_content
        #}

        result = self.page_sample(url)
        print(f"DEBUG: sample page result = \n{result['markdown_result']}")
        user_choice = input("Enter your choice: ").strip()
        #csv_list = ["TMP75","TMP117","TMP275"]
        csv_list = ["TMP117"]

        analisys = self.search_for_filters(result["html_to_analize"], user_choice)
        filtered_data = self.filtered_scraping(url, analisys, result["page_sample_result"], csv_list, user_choice)

        end = time.time()
        
        prompt_tokens = sum(t["prompt_tokens"] for t in self.extractor.tokens_usage)
        output_tokens = sum(t["output_tokens"] for t in self.extractor.tokens_usage)
        total_tokens = sum(t["total_tokens"] for t in self.extractor.tokens_usage)

        print("\n===== DEBUG: TOKEN USAGE =====")
        print("\n=== GEMINI ===")
        print(f"Prompt tokens : {prompt_tokens:,}")
        print(f"Output tokens : {output_tokens:,}")
        print(f"Total tokens  : {total_tokens:,}")
        
        print(f"DEBUG: test time: {end - start}")

        # print(f"\nDEBUG: result = \n{result}")
        print(f"DEBUG: sample page result = \n{result['markdown_result']}")
        print(f"DEBUG: analized html result = \n{analisys}")
        print(f"DEBUG: analized html result = \n{filtered_data}")
        return 1