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
            "markdown_result": self.extractor.suggestion(page["markdown"]),
            "html_to_analize": page["html"]
        }

        return result
    
    def search_for_filters(self, html: str, search_term: str):
        """ Analizes html content and returns a list of filters found on the page. """
        result = self.extractor.analize_html(html, search_term)

        return result

    def filtered_scraping(self, url: str, user_prompt: str, contents: dict, csv_list: list[str]) -> dict:

        filtered_data = self.firecrawl.fetch_with_filters(url, user_prompt, contents, csv_list)

        raise NotImplementedError("filtered_scraping() is not yet implemented. Please implement it to process the filtered_data and return the desired output.")
    
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

        #csv_list = ["TMP75","TMP117","TMP275"]
        csv_list = ["TMP117"]

        start = time.time()

        result = self.page_sample(url)
        # result = {'entity_type': 'product', 'available_fields': ['Solution name', 'Product number', 'Price/Quantity (USD)', 'Operating temperature range (°C)', 'Rating', 'Package size (L x W) (mm)', 'Package type', 'Pin count', 'TI functional safety category', 'Package area (mm^2)', 'Subcategory', 'Status'], 'total_records_declared': 396, 'records_per_page_declared': 50, 'pagination_detected': True, 'pagination_type': 'click_button', 'pagination_evidence': 'Page 1 of 8', 'filter_detected': True, 'filter_type': 'ui_interaction', 'filter_options': [{'name': 'Subcategory', 'example_values': None}, {'name': 'Rating', 'example_values': None}, {'name': 'Package type', 'example_values': None}, {'name': 'Pin count', 'example_values': None}, {'name': 'TI functional safety category', 'example_values': None}, {'name': 'Operating temperature range (°C)', 'example_values': None}, {'name': 'Package area (mm^2)', 'example_values': None}, {'name': 'Price/Quantity (USD)', 'example_values': None}, {'name': 'Status', 'example_values': None}], 'notes': "The page displays a table of sensor products. Pagination is explicitly indicated by the 'Page 1 of 8' label at the bottom. Filtering appears to be controlled via a UI interaction panel (parametric-filter).", 'non_dev_summary': "I found a list of 396 sensors, which are spread across 8 pages. You can view key details for each, such as the product name, price, operating temperature, and physical size. There are various filters available through the website's interface to help you narrow down your search, such as filtering by package type or subcategory. Everything looks ready to go, so just let me know if you'd like me to start gathering the details for you!"}
        
        user_choice = "Hall-effect current sensors"
        analized_html = self.search_for_filters(result["html_to_analize"], user_choice)

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
        print(f"DEBUG: sample page result = \n{result}")
        print(f"DEBUG: analized html result = \n{analized_html}")
        return 1