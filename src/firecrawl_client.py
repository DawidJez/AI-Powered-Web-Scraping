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

# the slowest grid we scrape refreshes over AJAX - scraping too early returns the previous page
PAGINATION_WAIT_MS = 6000

# a scrape is rejected outright when the waits add up past a minute - the clock is a budget like the action count
MAX_WAIT_MS = 60000

class FirecrawlClient:

    def __init__(self, api_key: str | None = None, debug: bool = False):
        self.app = FirecrawlApp(api_key=api_key or os.getenv("FIRECRAWL_API_KEY"))
        self.debug = debug

    def _debug(self, message: str) -> None:
        """Action dumps are for chasing a problem, not for every production run."""
        if self.debug:
            print(message)
    
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

    SUBMIT_DISPATCH = re.compile(
        r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\.dispatchEvent\(\s*new\s+Event\(\s*(['\"])submit\2[^;]*;"
    )

    def _real_submit(self, script: str) -> str:
        """requestSubmit still fires a real submit event, so the handlers the model wanted run too."""
        return self.SUBMIT_DISPATCH.sub(
            lambda match: "(function(f) { if (f && f.requestSubmit) { f.requestSubmit(); } "
                          "else if (f) { f.submit(); } })(" + match.group(1) + ");",
            script,
        )

    VALUE_ASSIGNMENT = re.compile(
        r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*|\((?:[^()'\"]|'[^']*'|\"[^\"]*\")*\))*)\.value\s*=(?!=)\s*([^;]+);"
    )
    NATIVE_SETTER = (
        r"(function(el, v) { const proto = Object.getPrototypeOf(el); "
        r"const found = Object.getOwnPropertyDescriptor(proto, 'value'); "
        r"if (found && found.set) { found.set.call(el, v); } else { el.value = v; } })(\1, \2);"
    )

    def _use_native_setter(self, script: str) -> str:
        """The model knows the native-setter pattern but skips it half the time - rewrite it either way."""
        return self.VALUE_ASSIGNMENT.sub(self.NATIVE_SETTER, script)

    APPLY_SELECTOR = re.compile(r"querySelector(?:All)?\(\s*(['\"])(.+?)\1\s*\)")
    VALUE_PLACEHOLDER = re.compile(r"""(['"])__VALUE__\1|__VALUE__""")

    def _scoped_apply_script(self, script: str) -> str:
        """
        Apply buttons come in twins - one inside every filter panel plus a page-level one, and the
        model's selector hits whichever comes first. The page-level one is the button whose nearest
        checkbox-bearing ancestor holds the most of them, so pick that one at runtime.
        """
        # the clicked element is found by the last selector before the click, not by the first one
        found = [m.group(2) for m in self.APPLY_SELECTOR.finditer((script or "").split(".click(")[0])]

        if not found:
            return script

        selector = json.dumps(found[-1])

        return (
            "(function() { const all = [...document.querySelectorAll(" + selector + ")]; "
            "if (!all.length) { return 'no apply control found'; } "
            "const reach = (el) => { let node = el.parentElement; "
            "while (node) { const found = node.querySelectorAll('input[type=checkbox], [role=\"checkbox\"]').length; "
            "if (found) { return found; } node = node.parentElement; } return 0; }; "
            "const ranked = all.map(el => ({ el: el, visible: el.offsetParent !== null ? 1 : 0, reach: reach(el) })); "
            "ranked.sort((a, b) => b.visible - a.visible || b.reach - a.reach); "
            "ranked[0].el.click(); "
            "return 'apply clicked, candidates=' + all.length + ', reach=' + ranked[0].reach; })();"
        )

    NUMBERED_TARGET = re.compile(r"\[\s*([\w-]+)\s*=\s*(['\"])(\d+)\2\s*\]")

    def _advancing_next_script(self, script: str) -> str:
        """
        A next-page control picked by an attribute holding a page number stops working after the
        first click - that number belongs to the page we were on, and one page later the same
        selector lands on the current page's own button, which is disabled. So read the page the
        grid says it is on and take the one after it, falling back to counting the clicks.
        """
        # the clicked element is described by the last selector before the click, not by the first one
        found = list(self.NUMBERED_TARGET.finditer((script or "").split(".click(")[0]))

        if not found:
            return script

        match = found[-1]

        attribute = json.dumps(match.group(1))
        start = int(match.group(3))
        original = json.dumps(script)

        return (
            "(function() { const attr = " + attribute + ", start = " + str(start) + "; "
            "const all = [...document.querySelectorAll('[' + attr + ']')]; "
            "if (!all.length) { return (0, eval)(" + original + "); } "
            "const page = (el) => parseInt(el.getAttribute(attr), 10); "
            "const marked = '[data-current=\"true\"], [aria-current]:not([aria-current=\"false\"]), "
            "[aria-selected=\"true\"], .is-currentpage, .is-current, .current, .active, .is-active'; "
            "const current = all.find(el => el.matches(marked) || (el.disabled && /^[0-9]+$/.test(el.textContent.trim()))); "
            "let wanted; "
            "if (current && !isNaN(page(current))) { wanted = page(current) + 1; } "
            "else { window.__nextPageStep = (window.__nextPageStep || 0) + 1; wanted = start + window.__nextPageStep - 1; } "
            "const target = all.find(el => page(el) === wanted && el !== current && !el.disabled); "
            "if (!target) { return 'no control for ' + attr + '=' + wanted; } "
            "target.click(); "
            "return 'paged to ' + attr + '=' + wanted; })();"
        )

    def _report_script_errors(self, result) -> None:
        """A guarded script that blew up answers with its message instead of crashing - say so."""
        returns = (getattr(result, "actions", None) or {}).get("javascriptReturns") or []

        for entry in returns:
            value = entry

            while isinstance(value, dict):
                value = value.get("value")

            if isinstance(value, str) and value.startswith("script error"):
                print(f"WARNING: {value}")

    EVENT_DISPATCH = re.compile(r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\.dispatchEvent\(\s*new\s+Event\(")
    INPUT_EVENT = re.compile(r"new\s+Event\(\s*(['\"])input\1")

    def _confirm_with_enter(self, script: str) -> str:
        """
        A real keystroke fires keydown and keyup around the input event, and these components often
        listen for those - dispatching 'input' alone leaves the text sitting in the field.
        """
        typed = bool(script) and bool(self.INPUT_EVENT.search(script))
        submits = "requestSubmit" in (script or "") or ".submit(" in (script or "")

        if not typed or submits or "KeyboardEvent" in script:
            return script

        matches = list(self.EVENT_DISPATCH.finditer(script))

        if not matches:
            return script

        last = matches[-1]
        opening = script.index("(", script.index(".dispatchEvent", last.start()))
        depth, end = 0, None

        for index in range(opening, len(script)):
            if script[index] == "(":
                depth += 1
            elif script[index] == ")":
                depth -= 1

                if depth == 0:
                    end = index + 1
                    break

        if end is None:
            return script

        if script[end:end + 1] == ";":
            end += 1

        receiver = last.group(1)
        keys = "".join(
            f" {receiver}.dispatchEvent(new KeyboardEvent('{name}', "
            "{ bubbles: true, composed: true, key: 'Enter' }));"
            for name in ("keydown", "keyup")
        )

        return script[:end] + keys + script[end:]

    SUBMIT_IN_COMPONENT = ('button[type="submit"], input[type="submit"], [class*="submit"], '
                           '[class*="search-button"], [aria-label*="search" i], [aria-label*="submit" i]')

    # a search box keeps its clear and close controls next to the one we want
    NOT_A_SUBMIT = '[class*="clear"], [class*="reset"], [class*="close"], [aria-label*="clear" i], [aria-label*="close" i]'

    def _confirm_with_button(self, script: str) -> str:
        """
        A field inside a shadow root often confirms with its own button, and that button is the one
        thing neither the fetched HTML nor the model can see - so look for it in the live page.
        """
        typed = bool(script) and bool(self.INPUT_EVENT.search(script))
        confirms = ".click(" in (script or "") or "requestSubmit" in (script or "") or ".submit(" in (script or "")

        # outside a shadow root the model reads the DOM as well as we do - leave those scripts alone
        if not typed or confirms or ".shadowRoot" not in script:
            return script

        found = [m.group(2) for m in self.APPLY_SELECTOR.finditer(script)]

        if not found:
            return script

        component = json.dumps(found[0])
        wanted = json.dumps(self.SUBMIT_IN_COMPONENT)
        skip = json.dumps(self.NOT_A_SUBMIT)

        return script + (
            " (function() { const host = document.querySelector(" + component + "); if (!host) { return; } "
            "for (const root of [host.shadowRoot, host]) { if (!root) { continue; } "
            "const button = [...root.querySelectorAll(" + wanted + ")].find(el => !el.matches(" + skip + ")); "
            "if (button) { button.click(); return; } } })();"
        )

    def _safe_script(self, script: str) -> str:
        """One crashing executeJavascript kills the whole action sequence, so a bad selector becomes a no-op instead."""
        script = self._confirm_with_button(self._confirm_with_enter(self._real_submit(self._use_native_setter(script))))

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

            # one unmatched filter is not a reason to lose the whole run - the no-op guard still watches the result
            if not matched_any:
                print(f"WARNING: none of the values {selected_values} matched an option - this filter will not be applied")
                return []

        elif final_script:
            if not selected_values:
                raise ValueError("DEBUG: identifier filter has no value to type")
            text = selected_values[0] if len(selected_values) == 1 else ", ".join(selected_values)

            # the model quotes the placeholder either way, so the quotes are part of what we replace
            script = self.VALUE_PLACEHOLDER.sub(lambda _: json.dumps(text), final_script)

            if "__VALUE__" in script:
                print(f"WARNING: could not substitute the value into the filter script - it still holds __VALUE__")

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
                {"type": "executeJavascript", "script": self._safe_script(self._scoped_apply_script(apply_script))},
                {"type": "wait", "milliseconds": 1500}
            ]
        return [{"type": "wait", "milliseconds": 700}]


    def _label_for(self, category_entries: list[dict], extra_value: str | None) -> str:
        parts = [f"{e['name']}={','.join(e.get('values') or [])}" for e in category_entries]

        if extra_value is not None:
            parts.append(extra_value)

        return "; ".join(parts)

    def _run_fetch(self, url: str, category_actions: list, extra_actions: list, apply_actions: list, item_label: str, identifier_value: str | None, wait_for: int = 3000) -> dict:
        actions = category_actions + extra_actions + apply_actions
        if not actions:
            raise ValueError("No filters resolved to any action")

        self._debug(f"DEBUG: actions: {actions}")

        result = self.app.scrape_url(
            url=url,
            formats=['markdown', 'links'],
            wait_for=wait_for,
            actions=actions,
            proxy="auto"
        )

        self._report_script_errors(result)
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

        self._report_script_errors(result)

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

        spent_wait = wait_for + sum(a.get("milliseconds", 0) for a in actions if a["type"] == "wait")
        wait_budget = MAX_WAIT_MS - spent_wait - PAGINATION_WAIT_MS
        max_cycles = min(max_cycles, max(0, wait_budget // PAGINATION_WAIT_MS))

        # the first page is already in hand - with nothing left to page through there is nothing to scrape
        if max_cycles == 0:
            self._debug("DEBUG pagination: no further pages to fetch")
            return []

        js_index = sum(1 for a in actions if a["type"] == "executeJavascript")
        disabled_js_indices = []

        for _ in range(max_cycles):
            if disabled_check_script:
                actions.append({"type": "executeJavascript", "script": self._safe_value_script(disabled_check_script)})

                disabled_js_indices.append(js_index)
                js_index += 1

            actions.append({"type": "executeJavascript", "script": self._safe_script(self._advancing_next_script(pagination_script))})
            js_index += 1

            actions.append({"type": "wait", "milliseconds": PAGINATION_WAIT_MS})
            actions.append({"type": "scrape"})

        self._debug(f"DEBUG: actions: {actions}")

        result = self.app.scrape_url(
            url=url,
            formats=['markdown', 'links'],
            wait_for=wait_for,
            actions=actions,
            proxy="auto"
        )

        self._report_script_errors(result)

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

        self._debug(f"DEBUG pagination: {len(raw_scrapes)} scrapes -> {len(pages)} unique pages")

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
            self._debug(f"DEBUG pagination: stop_at={stop_at} (diagnostic only, not used for trimming)")

        dropped = len(raw_scrapes) - len(pages)

        # the flag is too shaky to skip the cycles, but it is good enough to tell a dead end
        # from a failure once the pages are in - one is worth reporting, the other is not
        if dropped and stop_at == 0:
            self._debug(f"DEBUG pagination: no page past the first one - {dropped} repeat(s) of it were dropped")

        elif dropped:
            print(f"WARNING: {dropped} paginated page(s) came back unchanged and were dropped - "
                  f"the next-page click did not advance, or the grid was still refreshing after {PAGINATION_WAIT_MS}ms")

        if self.debug:
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