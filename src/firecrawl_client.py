import json
import re
from firecrawl import FirecrawlApp
from dotenv import load_dotenv
import os

load_dotenv()

class FirecrawlClient:

    def __init__(self, api_key: str | None = None):
        self.app = FirecrawlApp(api_key=api_key or os.getenv("FIRECRAWL_API_KEY"))

    def fetch_page(self, url: str, scroll_rounds: int = 10, wait_for: int = 3000) -> dict:

        actions = []

        for _ in range(scroll_rounds):
            actions.append({"type": "scroll", "direction": "down"})
            actions.append({"type": "wait", "milliseconds": 800})

        result = self.app.scrape_url(
            url=url,
            formats=['markdown', 'html', 'links'],
            wait_for=wait_for,
            actions=actions
        )

        return {
            "url": url,
            "markdown": result.markdown,
            "html": result.html,
            "links": result.links,
            "metadata": result.metadata.model_dump() if result.metadata else None,
        }                            # dict type data
    
    # in further improvements the pages_count and id_termn will be returned 
    # from the first page (by llm orchestrator) and used to fetch all pages automatically, 
    # instead of hardcoding them in the function call
    def fetch_all_pages(self, url: str, pages_count: int = 8, id_term: str = "[aria-label*='Next' i]", wait_for: int = 3000) -> list[dict[str, str]]:
        all_pages = []

        for page_num in range(pages_count):
            actions = []
        
            for _ in range(page_num):
                actions.append({"type": "click", "selector": id_term})
                actions.append({"type": "wait", "milliseconds": 800})

            result = self.app.scrape_url(
                url=url, 
                formats=["markdown", "html"], 
                wait_for=wait_for,
                actions=actions
            )

            all_pages.append({"markdown": result.markdown, "html": result.html})

            print(f"DEBUG: strona {page_num + 1} pobrana")

        return all_pages

    def merge_pages(self, pages: list[dict[str, str]]) -> dict[str, str]:
        # Merge multiple paginated page results into a single markdown/html document.
        if not pages:
            return {"markdown": "", "html": ""}

        merged_markdown = "\n\n".join(
            page.get("markdown", "").strip() for page in pages if page.get("markdown")
        )
        merged_html = "\n".join(
            page.get("html", "").strip() for page in pages if page.get("html")
        )

        return {
            "markdown": merged_markdown,
            "html": merged_html,
        }
    
    def fetch_data_ai(self, url: str, user_prompt: str, page_sample: dict, csv_list: list[str], model: str = "spark-1-pro", max_credits = 300):
        mpns = json.dumps(csv_list)
        print(f"DEBUG: csv_list = {mpns}")
        print("DEBUG: I'm here in fetch_data_ai()")

        prompt = (f"""
            Context - here is a sample of the target page's content, to help you 
            understand its structure and where to look:\n{page_sample}\n
            Requested MPN list (csv_list): {mpns}
            Task: {user_prompt}\n\n
            The target page is: {url}
        """)

        result = self.app.agent(
            prompt=prompt,
            model=model,
            max_credits=max_credits
        )
        print(f"DEBUG: agent credits used: {getattr(result, 'credits_used', 'unknown')}")

        return result
