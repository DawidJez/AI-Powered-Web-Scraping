import json
import re
import hashlib
import soupsieve
from firecrawl import FirecrawlApp
from dotenv import load_dotenv
import os
import difflib
from itertools import product

load_dotenv()

# these controls allow one value at a time - needed in selecting which interaction to perform
EXCLUSIVE_INTERACTIONS = {"click_option", "select_dropdown"}

class FirecrawlClient:

    def __init__(self, api_key: str | None = None):
        self.app = FirecrawlApp(api_key=api_key or os.getenv("FIRECRAWL_API_KEY"))
    
    def fetch_page(self, url: str, scroll_rounds: int = 10, wait_for: int = 5000) -> dict:

        actions = []

        for _ in range(scroll_rounds):
            actions.append({"type": "scroll", "direction": "down", "amount": 2000})
            actions.append({"type": "wait", "milliseconds": 800})

        actions.append({"type": "wait", "milliseconds": 3000})
            
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

    # a plain `el.value = x` is swallowed by React/Vue controlled inputs - the native setter is not
    VALUE_ASSIGNMENT = re.compile(r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\.value\s*=(?!=)\s*([^;]+);")
    NATIVE_SETTER = (
        "(function(el, v) { const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype "
        ": (el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype); "
        "Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v); })(\\1, \\2);"
    )

    def _use_native_setter(self, script: str) -> str:
        """The model knows the native-setter pattern but skips it half the time - rewrite it either way."""
        return self.VALUE_ASSIGNMENT.sub(self.NATIVE_SETTER, script)

    def _safe_script(self, script: str) -> str:
        """One crashing executeJavascript kills the whole action sequence, so a bad selector becomes a no-op instead."""
        script = self._use_native_setter(script)

        return "(function() { try { " + script + " } catch (e) { return 'script error: ' + e.message; } })();"

    def _safe_value_script(self, script: str) -> str:
        """Same guard where we need the value back - the script is passed as data, so even a syntax error stays catchable."""
        payload = json.dumps(script)

        # an IIFE evaluates as an expression, a bare statement block only as a function body - read-only checks, safe to try twice
        return ("(function() { try { return (0, eval)(" + payload + "); } "
                "catch (e) { try { return (new Function(" + payload + "))(); } catch (e2) { return null; } } })();")

    def _usable_selector(self, selector: str | None) -> bool:
        """A native click needs plain CSS - engine extensions never reach the browser."""
        if not selector:
            return False

        lowered = selector.lower()

        # playwright/jquery text matchers look like CSS but no browser parses them
        if any(bad in lowered for bad in (":has-text", ":contains", ":-soup-contains", ":visible", ">>", "xpath=")):
            return False

        try:
            soupsieve.compile(selector)
        except Exception:
            return False

        return True

    def _resolve_choosen_entry(self, available_options: list, wanted_value: str, cutoff: float = 0.85):
        # same match, but the whole option - the selector matters, not only the script
        # cutoff stays high on purpose: at 0.4 "TLMG1100" matched "VLMK3100" (ratio 0.62),
        # and matching lowercased keeps a high cutoff from failing on case alone
        lowered = {opt["value"].lower(): opt for opt in available_options}
        match = difflib.get_close_matches(wanted_value.lower(), list(lowered), n=1, cutoff=cutoff)

        if not match:
            return None

        return lowered[match[0]]
    
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

        gate_selectors = resolved.get("gate_selectors") or []

        for index, gate_script in enumerate(gate_scripts):
            gate_selector = gate_selectors[index] if index < len(gate_selectors) else None

            # menus built by grid components often ignore a scripted click and open only on a real one
            if self._usable_selector(gate_selector):
                actions.append({"type": "click", "selector": gate_selector})
            else:
                actions.append({"type": "executeJavascript", "script": self._safe_script(gate_script)})

            actions.append({"type": "wait", "milliseconds": 800})

        if available_options:
            if not selected_values:
                raise ValueError("DEBUG: category filter has no values to select")
            
            matched_any = False

            for value in selected_values:
                chosen = self._resolve_choosen_entry(available_options, value)

                # skipping beats filtering by a wrong value the user never asked for
                if not chosen:
                    print(f"WARNING: no option matching '{value}' - this value will not be applied")
                    continue

                # a real browser click - some frameworks ignore scripted ones completely
                if self._usable_selector(chosen.get("selector")):
                    actions.append({"type": "click", "selector": chosen["selector"]})
                else:
                    if chosen.get("selector"):
                        print(f"WARNING: unusable selector for '{value}', falling back to the script: {chosen['selector']}")

                    actions.append({"type": "executeJavascript", "script": self._safe_script(chosen["script"])})

                actions.append({"type": "wait", "milliseconds": 1500})
                matched_any = True

            if not matched_any:
                raise ValueError(f"DEBUG: none of the values {selected_values} matched an available option")

        elif final_script:
            if not selected_values:
                raise ValueError("DEBUG: identifier filter has no value to type")
            text = selected_values[0] if len(selected_values) == 1 else ", ".join(selected_values)

            script = final_script.replace('"__VALUE__"', json.dumps(text))
            actions.append({"type": "executeJavascript", "script": self._safe_script(script)})
            actions.append({"type": "wait", "milliseconds": 1500})
        else:
            raise ValueError("DEBUG: resolved filter has neither a final_script nor available_options")

        return actions

    def _apply_action(self, resolved_filters: list[dict]) -> list:
        # only first apply script required cuz first occurence is passed to every other find_script in resolve_scripts
        apply_script = next((e["resolved"].get("apply_script") for e in resolved_filters if e["resolved"].get("apply_script")), None)

        if apply_script:
            return [
                {"type": "executeJavascript", "script": self._safe_script(apply_script)},
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

        print(f"DEBUG: actions: {actions}")

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

        # an exclusive control drops the other options from the DOM after the first real click,
        # so its values can't share one sequence - each needs its own fetch
        exclusive_entries = [
            entry for entry in category_entries
            
            if entry["resolved"].get("interaction_type") in EXCLUSIVE_INTERACTIONS
            and len(entry.get("values") or []) > 1
        ]

        if len(exclusive_entries) > 1:
            raise NotImplementedError("More than one multi-value exclusive category filter is not supported yet")

        exclusive_entry = exclusive_entries[0] if exclusive_entries else None

        category_actions = []
        for entry in category_entries:
            values = entry.get("values") or []

            if not values or entry is exclusive_entry:
                continue
            category_actions.extend(self._build_actions_for_filter(entry["resolved"], values))

        if exclusive_entry:
            # one identifier value is just a constant extra action, N of them would be a product
            if identifier_entries and len(identifier_entries[0].get("values") or []) > 1:
                raise NotImplementedError("An exclusive category filter combined with a multi-value identifier filter is not supported yet")

            identifier_extra = []
            identifier_value = None

            if identifier_entries:
                identifier_values = identifier_entries[0].get("values") or []

                if identifier_values:
                    identifier_value = identifier_values[0]
                    identifier_extra = self._build_actions_for_filter(identifier_entries[0]["resolved"], [identifier_value])

            results = []
            for value in exclusive_entry.get("values") or []:
                try:
                    extra = self._build_actions_for_filter(exclusive_entry["resolved"], [value])
                except ValueError as error:
                    print(f"WARNING: skipping value '{value}' - {error}")
                    continue

                label = self._label_for(category_entries, value)
                fetched = self._run_fetch(url, category_actions, extra + identifier_extra, apply_actions, label, identifier_value)

                # pagination has to rebuild the filters, and it needs to know which value this page used
                fetched["exclusive_value"] = value
                results.append(fetched)

            if not results:
                raise ValueError(f"DEBUG: no value of '{exclusive_entry.get('name')}' matched an available option")

            return results

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
        """
        actions = self._build_multi_filter_actions(resolved_filters, selected_values)

        if apply_script:
            actions.append({"type": "executeJavascript", "script": self._safe_script(apply_script)})
            actions.append({"type": "wait", "milliseconds": 1500})
        else:
            actions.append({"type": "wait", "milliseconds": 700})

        print(f"DEBUG: actions: {actions}")

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
        # scripts come in from the outside here too, so they get the same crash guard
        actions = [
            {**action, "script": self._safe_script(action["script"])} if action.get("type") == "executeJavascript" else action
            for action in actions
        ]

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
            exclusive_value: str | None = None,
            wait_for: int = 3000
        ):
        """
        Pagination loop size is capped to stay under Firecrawl's 50-action limit; 
        the real end of pagination is detected via
        disabled_check_script + result.actions["javascriptReturns"], not via the
        AI-guessed page_count.
        """
        actions = []

        category_entries = [e for e in resolved_filters if e["resolved"].get("available_options")]
        identifier_entries = [e for e in resolved_filters if not e["resolved"].get("available_options") and e["resolved"].get("final_script")]

        for entry in category_entries:
            values = entry.get("values") or []
            if not values:
                continue

            # this page came from one value of an exclusive filter - rebuild that one, not all
            if exclusive_value and entry["resolved"].get("interaction_type") in EXCLUSIVE_INTERACTIONS and exclusive_value in values:
                values = [exclusive_value]

            actions.extend(self._build_actions_for_filter(entry["resolved"], values))

        if identifier_entries and identifier_value:
            actions.extend(self._build_actions_for_filter(identifier_entries[0]["resolved"], [identifier_value]))

        actions.extend(self._apply_action(resolved_filters))

        pagination_script = resolved_pagination["next_button_script"]
        disabled_check_script = resolved_pagination.get("disabled_check_script")

        MAX_ACTIONS = 50
        SAFETY_MARGIN = 2

        actions_per_cycle = 4 if disabled_check_script else 3
        available_budget = MAX_ACTIONS - len(actions) - SAFETY_MARGIN
        max_cycles = max(0, available_budget // actions_per_cycle)
        max_cycles = min(max_cycles, page_count - 1) if page_count else max_cycles

        # the first page is already in hand - with nothing left to page through there is nothing to scrape
        if max_cycles == 0:
            print("DEBUG pagination: no further pages to fetch")
            return []

        js_index = sum(1 for a in actions if a["type"] == "executeJavascript")
        disabled_js_indices = []

        for _ in range(max_cycles):
            if disabled_check_script:
                actions.append({"type": "executeJavascript", "script": self._safe_value_script(disabled_check_script)})

                disabled_js_indices.append(js_index)
                js_index += 1

            actions.append({"type": "executeJavascript", "script": self._safe_script(pagination_script)})
            js_index += 1

            actions.append({"type": "wait", "milliseconds": 3500})
            actions.append({"type": "scrape"})

        print(f"DEBUG: actions: {actions}")

        result = self.app.scrape_url(
            url=url,
            formats=['markdown', 'links'],
            wait_for=wait_for,
            actions=actions,
            proxy="auto"
        )

        # Duplicate pages are dropped by content, not by the disabled flag: a site can
        # keep its pagination buttons disabled while the grid refreshes (Molex does),
        # so the flag is only a hint. Identical HTML means the click changed nothing.
        pages = []
        seen_hashes = set()
        raw_scrapes = result.actions["scrapes"]

        for scrape in raw_scrapes:
            page_hash = hashlib.md5(scrape["html"].encode("utf-8")).hexdigest()
            if page_hash in seen_hashes:
                continue
            seen_hashes.add(page_hash)
            pages.append({"markdown": None, "html": scrape["html"]})

        print(f"DEBUG pagination: {len(raw_scrapes)} scrapes -> {len(pages)} unique pages")

        stop_at = None
        if disabled_check_script:
            js_returns = result.actions.get("javascriptReturns") or []

            for cycle_i, ret_idx in enumerate(disabled_js_indices):
                if ret_idx >= len(js_returns):
                    continue

                ret = js_returns[ret_idx]
                value = None

                if isinstance(ret, dict):
                    value = ret.get("value")

                    if isinstance(value, dict):
                        value = value.get("value")
                        
                    value = bool(value) if value is not None else False
                elif isinstance(ret, bool):
                    value = ret

                if value is True:
                    stop_at = cycle_i
                    break

            # diagnostics only - this flag is unreliable mid-refresh, it no longer trims
            print(f"DEBUG pagination: stop_at={stop_at} (diagnostic only, not used for trimming)")

        with open("data/debug_pagination_pages.json", "w", encoding="utf-8") as f:
            json.dump({
                "raw_scrapes": result.actions.get("scrapes", []),
                "pages": pages,
                "javascriptReturns": result.actions.get("javascriptReturns"),
                "disabled_js_indices": disabled_js_indices,
                "stop_at": stop_at if disabled_check_script else None,
                "actions": actions,
            }, f, ensure_ascii=False, indent=2)

        return pages

    def fetch_plain(self, url: str, wait_for: int = 3000) -> dict:
        result = self.app.scrape_url(
            url=url,
            formats=['markdown', 'links'],
            wait_for=wait_for,
            proxy="auto"
        )
        return {
            "markdown": result.markdown,
            "links": result.links,
            "metadata": result.metadata,
            "item": "no_filter",
            "identifier_value": None
        }