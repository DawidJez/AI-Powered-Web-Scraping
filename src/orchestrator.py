import time

from ai_extractor import AIExtractor
from firecrawl_client import FirecrawlClient


class Orchestrator:
    def __init__(self):
        self.extractor = AIExtractor()
        self.firecrawl = FirecrawlClient()

    def page_sample(self, url: str) -> dict:
        """ Fetchers a single/first page from url and asks AI 
        what is possible to extract from it. Returns a list of field names. """

        page = self.firecrawl.fetch_page(url)
        result = self.extractor.suggestion(page["markdown"])

        return result
    
    def test(self):
        url = "https://www.ti.com/product-category/sensors/products.html"
        user_prompt = """
            cos
        """
        start = time.time()

        result = self.page_sample(url)

        end = time.time()
        
        prompt_tokens = sum(t["prompt_tokens"] for t in self.extractor.tokens_usage)
        output_tokens = sum(t["output_tokens"] for t in self.extractor.tokens_usage)
        total_tokens = sum(t["total_tokens"] for t in self.extractor.tokens_usage)

        print("\n===== TOKEN USAGE =====")
        print(f"Prompt tokens : {prompt_tokens:,}")
        print(f"Output tokens : {output_tokens:,}")
        print(f"Total tokens  : {total_tokens:,}")
        
        print(f"DEBUG: test time: {end - start}")

        print(f"\nDEBUG: result = \n{result}")
        return 1