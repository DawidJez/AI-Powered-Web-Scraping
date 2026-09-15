import json
import re
from firecrawl import FirecrawlApp
from dotenv import load_dotenv
import os
import difflib
from itertools import product

load_dotenv()

class FirecrawlClient:

    def __init__(self, api_key: str | None = None):
        self.app = FirecrawlApp(api_key=api_key or os.getenv("FIRECRAWL_API_KEY"))

    def fetch_page(self, url: str, scroll_rounds: int = 10, wait_for: int = 3000) -> dict:

        actions = []

        for _ in range(scroll_rounds):
            actions.append({"type": "scroll", "direction": "down", "amount": 2000})
            actions.append({"type": "wait", "milliseconds": 800})
            
        result = self.app.scrape_url(
            url=url,
            formats=['markdown', 'html', 'links'],
            wait_for=wait_for,
            actions=actions,
            proxy="auto"
        )

        return {
            "url": url,
            "markdown": result.markdown,
            "html": result.html,
            "links": result.links,
            "metadata": result.metadata.model_dump() if result.metadata else None,
        }                            # dict type data

    # NOT USED
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
                actions=actions,
                proxy="auto"
            )

            all_pages.append({"markdown": result.markdown, "html": result.html})

        return all_pages

    # currently not used
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
    
    def _single_fetch(self,url: str, item: str, scripts: list, apply_script: str = None, wait_for: int = 3000) -> dict:
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
        actions.append({"type": "wait", "milliseconds": 800})
        actions.append({"type": "press", "key": "Enter"})
        actions.append({"type": "wait", "milliseconds": 1500})

        if apply_script:
            actions.append({"type": "executeJavascript", "script": apply_script})
            actions.append({"type": "wait", "milliseconds": 1500})
        else:
            actions.append({"type": "wait", "milliseconds": 700})

        result = self.app.scrape_url(
            url=url,
            formats=['markdown', 'links'],
            wait_for=wait_for,
            actions=actions,
            proxy="auto"
        )

        #with open("data/debug_markdown_dump.md", "w", encoding="utf-8") as f:
        #    f.write(result.markdown)

        return {
            "markdown": result.markdown,
            "links": result.links,
            "metadata": result.metadata
        }
    
    def _bulk_fetch(self,url: str, item_list: list, scripts: dict, apply_script: str = None, wait_for: int = 3000) -> dict:
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

        if apply_script:
            actions.append({"type": "executeJavascript", "script": apply_script})
            actions.append({"type": "wait", "milliseconds": 1500})
        else:
            actions.append({"type": "wait", "milliseconds": 700})

        result = self.app.scrape_url(
            url=url,
            formats=['markdown', 'links'],
            wait_for=wait_for,
            actions=actions,
            proxy="auto"
        )

        return {
            "markdown": result.markdown,
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
    
    def _category_fetch(self, url: str, scripts: list, choosen_script: str, apply_script: str = None, wait_for: int = 3000) -> dict:
        if not choosen_script:
            raise ValueError(f"DEBUG: no chosen_options, nothing to choose from for '{choosen_script}'")

        gate_scripts = scripts["gate_scripts"]
        
        actions = []
        for s in gate_scripts:
            actions.append({"type": "executeJavascript", "script": s})
            actions.append({"type": "wait", "milliseconds": 800})
    
        actions.append({"type": "executeJavascript", "script": choosen_script})
        actions.append({"type": "wait", "milliseconds": 1500})

        if apply_script:
            actions.append({"type": "executeJavascript", "script": apply_script})
            actions.append({"type": "wait", "milliseconds": 1500})
        else:
            actions.append({"type": "wait", "milliseconds": 700})

        result = self.app.scrape_url(
            url=url,
            formats=['markdown', 'links'],
            wait_for=wait_for,
            actions=actions,
            proxy="auto"
        )

        return {
            "markdown": result.markdown,
            "links": result.links,
            "metadata": result.metadata
        }

    def _build_actions_for_filter(self, resolved: dict, selected_values: list[str]) -> list:
        """Builds ONLY the gate + selection actions for one filter. apply_script is
        handled separately by the caller (once, at the end of the whole sequence)."""
        actions = []
        gate_scripts = resolved.get("gate_scripts") or []
        final_script = resolved.get("final_script")
        available_options = resolved.get("available_options") or []

        for s in gate_scripts:
            actions.append({"type": "executeJavascript", "script": s})
            actions.append({"type": "wait", "milliseconds": 800})

        if available_options:
            if not selected_values:
                raise ValueError("DEBUG: category filter has no values to select")
            
            for value in selected_values:
                chosen_script = self._resolve_choosen_option(available_options, value)

                if not chosen_script:
                    raise ValueError(f"DEBUG: no matching option found for '{value}'")
                
                actions.append({"type": "executeJavascript", "script": chosen_script})
                actions.append({"type": "wait", "milliseconds": 1500})

        elif final_script:
            if not selected_values:
                raise ValueError("DEBUG: identifier filter has no value to type")
            text = selected_values[0] if len(selected_values) == 1 else ", ".join(selected_values)

            script = final_script.replace('"__VALUE__"', json.dumps(text))
            actions.append({"type": "executeJavascript", "script": script})
            actions.append({"type": "wait", "milliseconds": 1500})
        else:
            raise ValueError("DEBUG: resolved filter has neither a final_script nor available_options")

        return actions

    def _apply_action(self, resolved_filters: list[dict]) -> list:
        # only first apply script required cuz first occurence is passed to every other find_script in resolve_scripts
        apply_script = next((e["resolved"].get("apply_script") for e in resolved_filters if e["resolved"].get("apply_script")), None)

        if apply_script:
            return [
                {"type": "executeJavascript", "script": apply_script},
                {"type": "wait", "milliseconds": 1500}
            ]
        return [{"type": "wait", "milliseconds": 700}]


    def _build_multi_filter_actions(self, resolved_filters: list[dict], selected_values: list[str]) -> list:
        """
        Build one combined action sequence for several filters in order.
        This mirrors the old single-filter behavior for each filter while keeping all
        actions in one scrape request.
        """
        if len(resolved_filters) != len(selected_values):
            raise ValueError("resolved_filters and selected_values must have the same length")

        actions = []
        for resolved, selected_value in zip(resolved_filters, selected_values):
            actions.extend(self._build_actions_for_filter(resolved, selected_value))
        return actions

    # helper for dropdown selectors cuz ai is messing with it
    def _build_select_script(select_selector: str, option_value: str) -> str:
        return f"""
            const select = document.querySelector('{select_selector}');
            const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
            setter.call(select, '{option_value}');
            select.dispatchEvent(new Event('input', {{ bubbles: true }}));
            select.dispatchEvent(new Event('change', {{ bubbles: true }}));
        """

    def _label_for(self, category_entries: list[dict], extra_value: str | None) -> str:
        parts = [f"{e['name']}={','.join(e.get('values') or [])}" for e in category_entries]

        if extra_value is not None:
            parts.append(extra_value)

        return "; ".join(parts)

    def _run_fetch(self, url: str, category_actions: list, extra_actions: list, apply_actions: list, item_label: str, identifier_value: str | None, wait_for: int = 3000) -> dict:
        actions = category_actions + extra_actions + apply_actions
        if not actions:
            raise ValueError("No filters resolved to any action")

        result = self.app.scrape_url(
            url=url,
            formats=['markdown', 'links'],
            wait_for=wait_for,
            actions=actions,
            proxy="auto"
        )
        return {
            "markdown": result.markdown,
            "links": result.links,
            "metadata": result.metadata,
            "item": item_label,
            "identifier_value": identifier_value
        }
    
    def fetch_filtered_data(self, url: str, resolved_filters: list[dict]) -> list[dict]:
        """
        Generalized fetching!
        - category filters (available_options present): ALL matched values clicked in ONE
        combined sequence, constant across every fetch below.
        - at most one identifier filter (final_script only) supported:
            0 values -> ignored
            1 value, or N values with bulk_search_supported -> folded into the single combined fetch
            N values, not bulk -> N separate fetches, each with the SAME category actions
            + one identifier value (this is the only case that multiplies fetches)
        """
        category_entries = []
        identifier_entries = []

        for entry in resolved_filters:
            resolved = entry["resolved"]
            has_options = bool(resolved.get("available_options"))
            has_final = bool(resolved.get("final_script"))

            if has_options:
                category_entries.append(entry)
            elif has_final:
                identifier_entries.append(entry)
            else:
                raise ValueError(f"DEBUG: filter '{entry.get('name')}' resolved with neither final_script nor available_options")

        if len(identifier_entries) > 1:
            raise NotImplementedError("Multiple identifier-type filters in one run is not supported yet")

        apply_actions = self._apply_action(category_entries + identifier_entries)

        category_actions = []
        for entry in category_entries:
            values = entry.get("values") or []

            if not values:
                continue
            category_actions.extend(self._build_actions_for_filter(entry["resolved"], values))

        if not identifier_entries:
            return [self._run_fetch(url, category_actions, [], apply_actions, self._label_for(category_entries, None), None)]

        identifier_entry = identifier_entries[0]
        identifier_values = identifier_entry.get("values") or []
        bulk_supported = bool(identifier_entry.get("bulk_search_supported"))

        if not identifier_values:
            return [self._run_fetch(url, category_actions, [], apply_actions, self._label_for(category_entries, None), None)]

        if len(identifier_values) == 1 or bulk_supported:
            extra = self._build_actions_for_filter(identifier_entry["resolved"], identifier_values)
            joined = identifier_values[0] if len(identifier_values) == 1 else ", ".join(identifier_values)
            return [self._run_fetch(url, category_actions, extra, apply_actions, self._label_for(category_entries, joined), joined)]

        results = []
        for value in identifier_values:
            extra = self._build_actions_for_filter(identifier_entry["resolved"], [value])
            results.append(self._run_fetch(url, category_actions, extra, apply_actions, self._label_for(category_entries, value), value))
        return results

    def fetch_multi_filtered_data(self, url: str, resolved_filters: list[dict], selected_values: list[str], apply_script: str = None, wait_for: int = 3000) -> dict:
        """
        Execute a single scrape that applies multiple filters in order using
        the combined action sequence built by `_build_multi_filter_actions`.
        Returns a dict compatible with other fetch helpers: `markdown`, `links`, `metadata`,
        and an `item` field representing the selected_values joined by comma.
        """
        actions = self._build_multi_filter_actions(resolved_filters, selected_values)

        if apply_script:
            actions.append({"type": "executeJavascript", "script": apply_script})
            actions.append({"type": "wait", "milliseconds": 1500})
        else:
            actions.append({"type": "wait", "milliseconds": 700})

        result = self.app.scrape_url(
            url=url,
            formats=['markdown', 'links'],
            wait_for=wait_for,
            actions=actions,
            proxy="auto"
        )

        separator = ", "
        joined = separator.join(selected_values)

        return {
            "markdown": result.markdown,
            "links": result.links,
            "metadata": result.metadata,
            "item": joined
        }
    
    def fetch_with_given_actions(self, url: str, actions: list, wait_for: int = 3000):
        
        result = self.app.scrape_url(
            url=url,
            formats=['html'],
            wait_for=wait_for,
            actions=actions,
            proxy="auto"
        )

        return {"html": result.html}    
    
    def fetch_with_pagination(
            self,
            url: str,
            resolved_filters: list[dict],
            resolved_pagination: dict,
            page_count: int,
            identifier_value: str | None,
            wait_for: int = 3000
        ):
        """
        Re-applies the same filters used for the original fetch before paging - each
        scrape_url call in Firecrawl starts from a fresh page load, so filters must be
        reconstructed from scratch.
        """
        actions = []

        category_entries = [e for e in resolved_filters if e["resolved"].get("available_options")]
        identifier_entries = [e for e in resolved_filters if not e["resolved"].get("available_options") and e["resolved"].get("final_script")]

        for entry in category_entries:
            values = entry.get("values") or []
            if not values:
                continue
            actions.extend(self._build_actions_for_filter(entry["resolved"], values))

        if identifier_entries and identifier_value:
            actions.extend(self._build_actions_for_filter(identifier_entries[0]["resolved"], [identifier_value]))

        actions.extend(self._apply_action(resolved_filters))

        pagination_script = resolved_pagination["next_button_script"]
        for _ in range(page_count - 1):
            actions.append({"type": "executeJavascript", "script": pagination_script})
            actions.append({"type": "wait", "milliseconds": 1500})
            actions.append({"type": "scrape"})

        print(f"DEBUG: actions: {actions}")

        result = self.app.scrape_url(
            url=url,
            formats=['markdown', 'links'],
            wait_for=wait_for,
            actions=actions,
            proxy="auto"
        )

        pages = []
        for scrape in result.actions["scrapes"]:
            pages.append({"markdown": None, "html": scrape["html"]})

        return pages