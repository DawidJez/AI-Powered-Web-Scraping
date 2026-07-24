import json
import re
from firecrawl import FirecrawlApp
from dotenv import load_dotenv
import os
import difflib

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
                actions.append({"type": "executeJavasript", "script": id_term})
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
    
    # gówno nie działa, za wolne, za głupie, za mało kredytów co kolwiek, jebał to pies trzeba własne kuźwa robić, 
    # niech to szlag jasny trafi (tą linie copilot napisał akurat, wie co jest na rzeczy)
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
    
    # WARNING: think what if there will be multiple pages, maybe ai should
    # check again if there is multiple pages and how to get/scroll to next page,
    # in this case we should also return the html for ai to analize;
    # jakby co to ja piszę takie fajne kom nie ai slop
    def _single_fetch(self,url: str, item: str, scripts: list, wait_for: int = 3000) -> dict:
        actions = []

        gate_scripts = scripts["gate_scripts"]
        final_script = scripts["final_script"]

        if gate_scripts:
            for s in gate_scripts:
                actions.append({"type": "executeJavascript", "script": s})
                actions.append({"type": "wait", "milliseconds": 800})

        actions.append({"type": "executeJavascript", "script": final_script})
        actions.append({"type": "wait", "milliseconds": 800})

        actions.append({"type": "write", "text": item})
        actions.append({"type": "wait", "milliseconds": 1500})

        result = self.app.scrape_url(
            url=url,
            formats=['markdown', 'links'],
            wait_for=wait_for,
            actions=actions
        )

        return {
            "records": result.markdown,
            "links": result.links,
            "metadata": result.metadata
        }
    
    def _bulk_fetch(self,url: str, item_list: list, scripts: dict, wait_for: int = 3000) -> dict:
        separator: str = ", "
        text_list = separator.join(item_list)

        actions = []

        gate_scripts = scripts["gate_scripts"]
        final_script = scripts["final_script"]

        if gate_scripts:
            for s in gate_scripts:
                actions.append({"type": "executeJavascript", "script": s})
                actions.append({"type": "wait", "milliseconds": 800})

        actions.append({"type": "executeJavascript", "script": final_script})
        actions.append({"type": "wait", "milliseconds": 800})

        actions.append({"type": "write", "text": text_list})
        actions.append({"type": "wait", "milliseconds": 1500})

        result = self.app.scrape_url(
            url=url,
            formats=['markdown', 'links'],
            wait_for=wait_for,
            actions=actions
        )

        return {
            "records": result.markdown,
            "links": result.links,
            "metadata": result.metadata
        }
    
    def _resolve_choosen_option(self, available_options: list, wanted_value: str):
        values = [opt["value"] for opt in available_options]
        match = difflib.get_close_matches(wanted_value, values, n=1, cutoff=0.4)

        if not match:
            return None
        
        matched_value = match[0]
        return next(o["script"] for o in available_options if o["value"] == matched_value)
    
    def _category_fetch(self, url: str, scripts: list, choosen_script: str, wait_for: int = 3000) -> dict:
        if not choosen_script:
            raise ValueError(f"DEBUG: brak choosen_options, nie ma z czego wybrac dla '{choosen_script}'")

        gate_scripts = scripts["gate_scripts"]
        
        actions = []
        for s in gate_scripts:
            actions.append({"type": "executeJavascript", "script": s})
            actions.append({"type": "wait", "milliseconds": 800})
    
        actions.append({"type": "executeJavascript", "script": choosen_script})
        actions.append({"type": "wait", "milliseconds": 1500})

        result = self.app.scrape_url(
            url=url,
            formats=['markdown', 'links'],
            wait_for=wait_for,
            actions=actions
        )

        return {
            "records": result.markdown,
            "links": result.links,
            "metadata": result.metadata
        }

    def fetch_filtered_data(self, url: str, resolved: list, filter: dict, items_list: list, available_options: list) -> list[dict]:
        filtered_data = []

        """
        scripts = [
            "button[aria-label='Open Filter Menu']",
            "ti-search-field[placeholder='Filter by product or part number']",
        ]
        print(scripts)
        """

        if filter["cardinality"]== "identifier":
            for item in items_list: 
                result = self._single_fetch(url, item, resolved)
                filtered_data.append(result)

        elif filter.get("bulk_search_supported"):
            result = self._bulk_fetch(url, items_list, resolved)
            filtered_data.append(result)

        elif filter["cardinality"]== "category":
            for value in items_list:
                choosen_script = self._resolve_choosen_option(available_options, value)

                #raise SystemError("DEBUG: Wanted selectors: #P7000 + coś, received selectors: " + ", ".join(selectors) + " and choosen selector " + choosen_selector)
            
                result = result = self._category_fetch(url, resolved, choosen_script)

                filtered_data.append(result)
        else:
            raise NotImplementedError("unrecognized filter cardinality")

        return filtered_data
    
    def fetch_with_given_actions(self, url: str, actions: list, wait_for: int = 3000):
        print(actions)
        result = self.app.scrape_url(
            url=url,
            formats=['html'],
            wait_for=wait_for,
            actions=actions
        )

        return {"html": result.html}    

    def test(self):
        result = self.app.scrape_url(
            url="https://www.ti.com/product-category/sensors/products.html",
            formats=['html'],
            actions=[
                {
                    "type": "executeJavascript",
                    "script": "return document.querySelectorAll('ti-search-field').length;"
                },
                {"type": "wait", "milliseconds": 800},
                {"type": "scrape"}  # <- to Ci da html PO kliknięciu, w actions.scrapes
            ]
        )
        print(result)