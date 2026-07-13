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
    
    def fetch_all_pages(self, url: str, id_pattern: str, max_pages: int = 20) -> list[str]:
        # for pagination, we will fetch multiple pages by clicking "Next" button and collecting HTML content
        all_pages = []
        seen_ids = set()

        for page_num in range(max_pages):
            actions = [{"type": "wait", "milliseconds": 1500}]

            for _ in range(page_num):
                actions.append({"type": "click", "selector": "[aria-label*='Next' i]"})
                actions.append({"type": "wait", "milliseconds": 1000})

            result = self.app.scrape_url(
                url=url,
                formats=["markdown", "html"],
                actions=actions
            )

            current_ids = set(re.findall(id_pattern, result.html))
            new_ids = current_ids - seen_ids

            print(
                f"DEBUG: strona {page_num + 1}, "
                f"unikalnych na tej stronie: {len(current_ids)}, "
                f"nowych: {len(new_ids)}"
            )

            if page_num > 0 and not new_ids:
                print("DEBUG: brak nowych rekordów - koniec paginacji")
                break

            seen_ids.update(current_ids)

            all_pages.append(
                {
                    "markdown": result.markdown,
                    "html": result.html,
                }
            )

        return all_pages