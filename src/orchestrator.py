import math
import time
import json
from pathlib import Path
import re
import os
from itertools import product

from bs4 import BeautifulSoup

from ai_extractor import AIExtractor
from firecrawl_client import FirecrawlClient
from excel_exporter import ExcelExporter
from pdf_downloader import PDFDownloader

class Orchestrator:
    def __init__(self, debug: bool = False):
        self.debug = debug
        self.extractor = AIExtractor(debug=debug)
        self.firecrawl = FirecrawlClient(debug=debug)
        self.excel = ExcelExporter()
        self.pdf = PDFDownloader()
        self.unread_pages = 0
        self.unfiltered_pages = 0

    def _debug(self, message: str) -> None:
        """The resolution dumps are for chasing a problem, not for the person using the tool."""
        if self.debug:
            print(message)

    def page_sample(self, url: str) -> dict:
        """ Fetches a single/first page from url and asks AI
        what is possible to extract from it. Returns a list of field names
        and analyzed html content required to filtered scraping. """

        page = self.firecrawl.fetch_page(url)
        result = {
            "page_sample_result": self.extractor.suggestion(page["markdown"]),
            "html_to_analize": page["html"]
        }

        return result
    
    def search_for_filters(self, html: str, search_terms:list):
        """ Analyses html content and returns a list of filters found on the page. """
        result = self.extractor.analize_html(html, search_terms)

        return result

    def _last_compound(self, selector: str | None) -> str | None:
        
        if not selector:
            return None

        depth, quote, start = 0, None, 0

        for index, char in enumerate(selector):
            if quote:
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == "[":
                depth += 1
            elif char == "]":
                depth = max(0, depth - 1)
            elif depth == 0 and (char.isspace() or char in ">+~"):
                start = index + 1

        return selector[start:].strip() or None

    def _selector_hits(self, html: str, selector: str | None) -> int:
        """How many elements the selector really hits - unusable ones count as zero."""
        if not selector or not html:
            return 0

        try:
            return len(BeautifulSoup(html, "html.parser").select(selector))
        except Exception:
            return 0

    def _selector_matches(self, html: str, selector: str | None) -> bool:
        """
        Does the selector hit anything in the HTML we analysed? A native click on an invented
        selector kills the whole Firecrawl request, so an unmatched one is dropped beforehand.
        """
        if not selector or not html:
            return False

        try:
            return bool(BeautifulSoup(html, "html.parser").select(selector))
        except Exception:
            return False

    def _is_closed_select_option(self, html: str, selector: str | None) -> bool:
        """
        An <option> of an ordinary dropdown is not a clickable element - the browser paints it only
        while the list is open, so a native click on it fails and takes the whole request down.
        """
        if not selector or not html:
            return False

        try:
            found = BeautifulSoup(html, "html.parser").select(selector)
        except Exception:
            return False

        if not found or any(el.name != "option" for el in found):
            return False

        for el in found:
            parent = el.find_parent("select")

            if parent is None or parent.has_attr("multiple"):
                return False

            # multiple/size renders the select as a list box, where the options really are clickable
            try:
                if int(parent.get("size", 1)) > 1:
                    return False
            except (TypeError, ValueError):
                return False

        return True

    # the quote style has to be matched by a backreference - selectors often carry the other kind inside
    SCRIPT_TARGET = re.compile(r"(querySelector(?:All)?|getElementById)\(\s*(['\"])(.+?)\2\s*\)")
    SCRIPT_TEXT = re.compile(r"includes\(\s*(['\"])(.+?)\1\s*\)")
    COLUMN_ATTRIBUTES = ("aria-colindex", "col-id", "data-colindex", "data-field")

    def _selectors_in_script(self, script: str | None) -> list[str]:
        """Every element the script looks up, in the order it looks them up."""
        return [
            "#" + match.group(3) if match.group(1) == "getElementById" else match.group(3)
            for match in self.SCRIPT_TARGET.finditer(script or "")
        ]

    def _script_targets_disabled(self, html: str | None, script: str | None, name: str = "") -> bool:
        """
        Grids ship one filter input per column and leave them disabled until the column menu
        opens - typing into one fails without a sound, so it is no control at all.
        """
        if not script or not html:
            return False

        for selector in self._selectors_in_script(script):

            # a bare tag name asks about every such element on the page, and the answer says
            # nothing about the one control the script means
            if not any(mark in selector for mark in "#.["):
                continue

            if self._is_disabled_in_html(html, selector):
                self._debug(f"DEBUG: '{name}' - the script targets a disabled control ({selector}), digging deeper")
                return True

        return False

    def _repair_gate_selector(self, analisys: dict, html: str, name: str) -> str | None:
        """
        A grid puts the header and its filter button in different rows, so the model's scoping by
        the header alone hits nothing. Both rows carry the same column attribute - scope by that.
        """
        script = analisys.get("gate_script") or ""
        selector = analisys.get("gate_selector") or ""
        wanted = self.SCRIPT_TEXT.search(script)

        # the control itself is the last part of the selector, and the last one the script looks up
        targets = [part for part in [self._last_compound(selector)] if part]
        targets += self._selectors_in_script(script)[-1:]

        if not targets or not html:
            return None

        label = wanted.group(2) if wanted else name
        soup = BeautifulSoup(html, "html.parser")

        # the smallest element carrying the label is the header itself, not a wrapper around it
        headers = [el for el in soup.find_all(True) if label in el.get_text()]

        if not headers:
            return None

        node = min(headers, key=lambda el: len(el.find_all(True)))

        # the label sits in a plain span, so the column attribute is on one of its ancestors
        while node is not None and getattr(node, "name", None):
            for attribute in self.COLUMN_ATTRIBUTES:
                value = node.get(attribute)

                if not value:
                    continue

                for target in targets:
                    candidate = f'[{attribute}="{value}"] {target}'

                    try:
                        if len(soup.select(candidate)) == 1:
                            return candidate
                    except Exception:
                        continue

            node = node.parent

        return None

    def _drop_invented_selectors(self, analisys: dict, html: str, name: str) -> None:
        """Keeps only the selectors that exist in this HTML - the rest fall back to their script."""
        
        hits = self._selector_hits(html, analisys.get("gate_selector"))

        # a native click takes the first match, so an ambiguous gate opens the wrong column's menu
        if analisys.get("gate_selector") and hits != 1:
            repaired = self._repair_gate_selector(analisys, html, name)
            trouble = "matches nothing" if hits == 0 else f"matches {hits} elements"

            if repaired:
                print(f"WARNING: gate selector for '{name}' {trouble} ({analisys['gate_selector']}) - rebuilt as {repaired}")
            else:
                print(f"WARNING: gate selector for '{name}' {trouble} in the page ({analisys['gate_selector']}) - using the script instead")

            analisys["gate_selector"] = repaired

        for option in analisys.get("available_options") or []:
            selector = option.get("selector")

            if selector and not self._selector_matches(html, selector):
                print(f"WARNING: option selector for '{option.get('value')}' matches nothing in the page - using the script instead")
                option["selector"] = None
                continue

            if selector and self._is_closed_select_option(html, selector):
                print(f"WARNING: option selector for '{option.get('value')}' points at an <option> of a plain dropdown - using the script instead")
                option["selector"] = None

    def _complete_missing_options(self, analisys: dict, html: str, wanted_values: list, name: str) -> None:
        """
        Long option lists come back truncated - if a requested value has a twin in the HTML built
        the same way as the listed ones, rebuild its entry instead of losing the whole filter.
        """
        options = analisys.get("available_options") or []

        if not options or not html or not wanted_values:
            return

        listed = {(option.get("value") or "").lower() for option in options}
        template = options[0]
        template_value = template.get("value") or ""
        template_script = template.get("script") or ""
        template_id = re.search(r"getElementById\('([^']+)'\)", template_script)
        soup = BeautifulSoup(html, "html.parser")

        for value in wanted_values:
            if not value or not template_value or value.lower() in listed:
                continue

            script, selector = None, None

            # the options of one facet share an id prefix and differ only by the value at the end
            if template_id and template_id.group(1).endswith(template_value):
                new_id = template_id.group(1)[: -len(template_value)] + value

                if soup.find(id=new_id):
                    script = template_script.replace(template_id.group(1), new_id)

            elif template.get("selector"):
                candidate = template["selector"].replace(template_value, value)

                if self._selector_matches(html, candidate):
                    selector = candidate
                    script = template_script.replace(f"'{template_value}'", f"'{value}'").replace(f'"{template_value}"', f'"{value}"')

            if script:
                print(f"WARNING: option '{value}' was missing from the model's list for '{name}' - rebuilt from the page")
                analisys["available_options"].append({"value": value, "script": script, "selector": selector})

    def _repair_facet_choice(self, analisys: dict, html: str, name: str, wanted_values: list) -> None:
        """
        The HTML windows are picked by search term, so a filter called "Type" also matches the
        "Component Type" panel and the model can settle on the wrong one. The page says which
        panel is which: the one whose heading IS the filter name.
        """
        options = analisys.get("available_options") or []

        if not html or not wanted_values:
            return

        listed = {(option.get("value") or "").lower() for option in options}
        missing = [value for value in wanted_values if value and value.lower() not in listed]

        # the model's panel holds every value we asked for, so there is nothing to argue with
        if not missing:
            return

        soup = BeautifulSoup(html, "html.parser")
        wanted = name.strip().lower()
        headings = [el for el in soup.find_all(True) if el.get_text(strip=True).lower() == wanted]

        if not headings:
            return

        for heading in headings:
            panel = heading.parent

            while panel is not None:
                found = [el for el in panel.find_all(id=True) if el.get("id", "").startswith("facet-")]

                if found:
                    break

                panel = panel.parent

            if panel is None:
                continue

            rebuilt = []

            for value in missing:
                for element in found:
                    if element["id"].endswith(value):
                        rebuilt.append({
                            "value": value,
                            "script": "(function() { const el = document.getElementById('" + element["id"] + "'); if (el) { el.click(); } })();",
                            "selector": None,
                        })
                        break

            if rebuilt:
                print(f"WARNING: the options for '{name}' came from another panel - rebuilt {[o['value'] for o in rebuilt]} from the one labelled '{name}'")
                analisys["available_options"] = rebuilt
                return

    def _is_disabled_in_html(self, html: str, selector: str | None) -> bool:
        """
        The AI is told to skip disabled twins but breaks that rule now and then, and typing
        into a disabled input fails silently.
        """
        if not selector or not html:
            return False

        try:
            found = BeautifulSoup(html, "html.parser").select(selector)
        except Exception:
            return False

        if not found:
            return False

        return all(el.has_attr("disabled") or el.get("aria-disabled") == "true" for el in found)

    def _restore_value_placeholder(self, script: str | None, values: list[str]) -> str | None:
        """
        The model is told to leave __VALUE__ in the typing script but bakes the first value in
        now and then - every value then gets the same filter and the run collapses to one.
        """
        if not script or "__VALUE__" in script:
            return script

        # longest first, so a short value that is a prefix of a longer one does not win
        for value in sorted(values or [], key=len, reverse=True):
            for quote in ('"', "'"):
                baked = quote + value + quote

                if baked in script:
                    print(f"WARNING: the typing script had '{value}' baked in instead of the placeholder - put it back")
                    return script.replace(baked, quote + "__VALUE__" + quote)

        print("WARNING: the typing script has no __VALUE__ and none of the requested values - "
              "it will be sent as the model wrote it")
        return script

    def resolve_scripts(self, url: str, analisys: dict, filter: dict, html: str | None = None, max_depth: int = 5) -> list:
        if analisys.get("confidence") == "none":
            raise ValueError(f"DEBUG: '{filter['name']}' not found in HTML, evidence: {analisys.get('evidence')}")

        if self._script_targets_disabled(html, analisys.get("target_script"), filter["name"]):

            analisys["target_script"] = None
            analisys["interaction_type"] = "unknown"
            analisys["gate_needed"] = True
        
        # options already enumerated in the first pass - no reason to resolve them again
        if analisys["gate_script"] and not analisys.get("target_script") and not analisys.get("available_options"):
            scripts = [analisys["gate_script"]]
            selectors = [analisys.get("gate_selector")]
            last_result = None # out target script

            for _ in range(1,max_depth):
                actions = []

                for index, s in enumerate(scripts):
                    selector = selectors[index] if index < len(selectors) else None

                    # same rule as the filter actions - a real click wherever we have a selector for it
                    if selector:
                        actions.append({"type": "click", "selector": selector})
                    else:
                        actions.append({"type": "executeJavascript", "script": s})

                    actions.append({"type": "wait", "milliseconds": 800})

                data_to_search = self.firecrawl.fetch_with_given_actions(url, actions) # we have to go deeper

                result = self.extractor.find_script(scripts, filter, data_to_search["html"], apply_script=analisys.get("apply_script"))

                # find_script gave up (retries exhausted or unusable response) - stop digging
                if not isinstance(result, dict):
                    self._debug(f"DEBUG: find_script returned no usable result for '{filter['name']}'")
                    break

                # a control that is disabled in the HTML we just fetched can't be used
                if self._is_disabled_in_html(data_to_search["html"], result.get("selector")):
                    self._debug(f"DEBUG: '{filter['name']}' - resolved control is disabled ({result.get('selector')}), digging deeper")
                    result["script"] = None
                    result["control_type"] = "none"
                    result["needs_more_depth"] = True

                if self._script_targets_disabled(data_to_search["html"], result.get("script"), filter["name"]):

                    result["script"] = None
                    result["control_type"] = "none"
                    result["needs_more_depth"] = True

                last_result = result

                if result["needs_more_depth"] == False and result.get("control_type") == "none":
                    #print("DEBUG: AI found nothing, aborting loop")
                    break

                candidate = result.get("script")

                # category-type field (available_options) - script may be null, but it doesn't mean there is nothing
                if candidate:    
                    if candidate in scripts:
                        #print(f"DEBUG: AI returned the same script we already have ('{candidate}') - we're stuck, aborting")
                        break

                    scripts.append(result["script"])

                    selector = result.get("selector")
                    selectors.append(selector if self._selector_matches(data_to_search["html"], selector) else None)

                if not result.get("needs_more_depth", False):
                    break

            final_script = last_result.get("script") if last_result else None
            available_options = last_result.get("available_options") if last_result else []

            # gate opened but nothing behind it - let the caller skip this filter
            if not final_script and not available_options:
                raise ValueError(f"DEBUG: '{filter['name']}' - gate opened but no usable control was resolved")

            dropped_last = bool(final_script and scripts and scripts[-1] == final_script)
            gate_scripts = scripts[:-1] if dropped_last else scripts
            gate_selectors = selectors[:-1] if dropped_last else selectors

            interaction_type = last_result.get("interaction_type") if last_result else "unknown"

            if interaction_type == "type":
                final_script = self._restore_value_placeholder(final_script, filter.get("values"))

            return {
                "gate_scripts": gate_scripts,
                "gate_selectors": gate_selectors,
                "final_script": final_script,
                "available_options": available_options,
                "interaction_type": interaction_type,
                "apply_script": result.get("apply_script") if isinstance(result, dict) else None
            }
        
        gate_scripts = [analisys["gate_script"]] if analisys.get("gate_script") else []
        gate_selectors = [analisys.get("gate_selector")] if analisys.get("gate_script") else []
        target_script = analisys.get("target_script")

        if analisys.get("interaction_type") == "type":
            target_script = self._restore_value_placeholder(target_script, filter.get("values"))

        return {
            "gate_scripts": gate_scripts,
            "gate_selectors": gate_selectors,
            "final_script": target_script,
            "available_options": analisys.get("available_options"),
            "interaction_type": analisys.get("interaction_type"),
            "apply_script": analisys.get("apply_script")
        }

    def filtered_scraping(self, url: str, page_sample_result: dict, filters: list[dict], html: str | None = None) -> dict:
        """filters: [{"name": ..., "hints": [...], "values": [...]}, ...] - always this shape now."""
        normalized_filters = self.normalize_filters(filters=filters)

        if html is None:
            html = page_sample_result.get("html_to_analize") if isinstance(page_sample_result, dict) else None
        if html is None:
            raise ValueError("HTML content is required for filter resolution")

        # the solution will work regardless of whether the user wants to download 
        # data with or without filters.
        if not normalized_filters:
            return {
                "filtered_data": [self.firecrawl.fetch_plain(url)],
                "resolved_scripts": [],
                "apply_script": None,
            }

        filter_options = page_sample_result.get("filter_options", [])
        available_fields = page_sample_result.get("available_fields", [])
        identifier_hints = ["number", "id", "code", "part", "sku", "mpn", "name"]

        resolved_filters = []
        skipped_filters = []
        known_apply_script = None

        for filter_def in normalized_filters:
            name = (filter_def.get("name") or "").strip()
            hints = list(filter_def.get("hints") or [])
            values = list(filter_def.get("values") or [])
            if not name:
                continue

            # exact match before substring: a page can carry both "Type" and "Component Type"
            # as separate filters, and substring alone silently binds the wrong one
            matched_filter = next((f for f in filter_options if name.lower() == f["name"].lower()), None)

            if matched_filter is None:
                matched_filter = next((f for f in filter_options if name.lower() in f["name"].lower()), None)

            filter = matched_filter

            if matched_filter is None:
                matched_field = next((f for f in available_fields if name.lower() == f.lower()), None)

                if matched_field is None:
                    matched_field = next((f for f in available_fields if name.lower() in f.lower()), None)

                # the model's own lists are not a guarantee - let the HTML have the last word
                if matched_field is None:
                    print(f"WARNING: filter '{name}' is not in the detected filter_options - looking for it in the page anyway")
                    matched_field = name

                matched_field_lc = matched_field.lower()
                if any(hint in matched_field_lc for hint in identifier_hints):
                    filter = {"name": matched_field, "cardinality": "identifier", "example_values": None}
                else:
                    filter = {"name": matched_field, "cardinality": "category", "example_values": None}

            # the name that actually exists on the page, plus every value distinctive enough
            # to pin down its own option
            search_terms = []
            for term in [filter["name"], name] + hints + [v for v in values if self._is_distinctive_value(v)]:
                term = (term or "").strip()
                
                if term and term.lower() not in [t.lower() for t in search_terms]:
                    search_terms.append(term)

            filter["search_terms"] = search_terms

            analisys = self.search_for_filters(html, search_terms)
            self._debug(f"DEBUG: analisys for filter '{name}': {analisys}")

            if isinstance(analisys, dict):
                self._drop_invented_selectors(analisys, html, name)
                self._complete_missing_options(analisys, html, values, name)
                self._repair_facet_choice(analisys, html, name, values)

            # unusable analysis (empty or wrong shape) - skip instead of crashing downstream
            if not isinstance(analisys, dict) or not analisys:
                print(f"WARNING: skipping filter '{name}' - the analysis came back empty")
                skipped_filters.append({"name": name, "reason": "empty analysis"})
                continue

            # apply_script is one global button for the whole filter panel - once any filter's
            # analysis finds it, feed it forward so later filters don't need to rediscover it
            if not analisys.get("apply_script") and known_apply_script:
                analisys["apply_script"] = known_apply_script

            # one filter the page doesn't expose shouldn't kill the whole run - the rest
            # of the filters still narrow the results, and the skipped ones are reported
            try:
                resolved = self.resolve_scripts(url, analisys, filter, html)
            except ValueError as error:
                print(f"WARNING: skipping filter '{name}' - {error}")
                skipped_filters.append({"name": name, "reason": str(error)})
                continue

            if not known_apply_script and resolved.get("apply_script"):
                known_apply_script = resolved["apply_script"]

            if not known_apply_script:
                has_category_filter = bool(resolved.get("available_options")) or any(
                    e["resolved"].get("available_options") for e in resolved_filters
                )

                if has_category_filter:
                    fallback = self.extractor.find_global_apply_script(html)
                    self._debug(f"DEBUG: fallback global apply_script search: {fallback}")

                    if fallback.get("confidence") != "none" and fallback.get("apply_script"):
                        known_apply_script = fallback["apply_script"]
                        resolved["apply_script"] = known_apply_script

            resolved_filters.append({
                "resolved": resolved,
                "name": name,
                "values": values,
                "hints": hints,
            })

        if not resolved_filters:
            raise ValueError(
                f"No valid filters resolved for scraping (skipped: {[s['name'] for s in skipped_filters]})"
            )

        if skipped_filters:
            print(f"WARNING: these filters were NOT applied: {[s['name'] for s in skipped_filters]}")

        self._debug(f"DEBUG: resolved_filters = {resolved_filters}")
        #return 0

        return {
            "filtered_data": self.firecrawl.fetch_filtered_data(url, resolved_filters),
            "resolved_scripts": resolved_filters,
            "apply_script": known_apply_script,
            "skipped_filters": skipped_filters,
        }

    def resolve_pagination(self, url: str, page_sample: dict, html: str) -> dict:
        safe_url = re.sub(r"[^a-zA-Z0-9_-]", "_", url) # sanitize url to create a valid filename
        filename = Path(f"data/resolved_pagination/resolved_pagination_{safe_url}.json")
        
        if filename.exists():
            age_seconds = time.time() - filename.stat().st_mtime

            if age_seconds < 86400: # 24h
                cached = json.loads(filename.read_text(encoding="utf-8"))

                if cached.get("confidence") != "none" and cached.get("next_button_script"):
                    return cached

        resolved_pagination = self.extractor.resolve_pagination(page_sample, html, url) or {}
        if "analog.com" in url:
            resolved_pagination = {
                "next_button_script": '(function() { document.querySelector(\'button[aria-label="Next page"]\').click(); })();',
                "disabled_check_script": '(function() { const btn = document.querySelector(\'button[aria-label="Next page"]\'); return !!(btn && btn.disabled); })();',
                "confidence": "high",
                "evidence": "hardcoded - virtualized grid, pagination not visible in static HTML/markdown"
            }

        if resolved_pagination.get("confidence") != "none" and resolved_pagination.get("next_button_script"):
            filename.parent.mkdir(parents=True, exist_ok=True)

            with open(filename, "w", encoding="utf-8") as f:
                f.write(json.dumps(resolved_pagination))

        return resolved_pagination

    def extract_data(
            self,
            url: str,
            filtered_data: dict,
            user_prompt: str,
            resolved_pagination: dict | None = None,
            available_fields: list[str] | None = None,
            unfiltered_total: int | None = None,
        ):
        if available_fields is None:
            available_fields = []

        # pages the model never answered for - counted so a partial result cannot pass as a full one
        self.unread_pages = 0
        self.unfiltered_pages = 0
        all_results = []
        data = filtered_data.get("filtered_data")
        filters_applied = bool(filtered_data.get("resolved_scripts"))

        for page_data in data:
            extracted = self.extractor.extract(page_data, user_prompt, available_fields)
            self._count_unread(extracted, "the filtered page")

            # still the whole catalog after filtering - the filter never took effect, and
            # paginating it would scrape the entire site once per value. one value out of many can
            # miss, so drop that page instead of losing the values that did work
            if filters_applied and unfiltered_total and extracted.get("total_records_declared") == unfiltered_total:
                missed = page_data.get("identifier_value") if isinstance(page_data, dict) else None
                self.unfiltered_pages += 1
                print(f"WARNING: the filter had no effect for '{missed or 'one of the values'}' - the page still "
                      f"reports {unfiltered_total} records, so it is dropped and will not be paginated")
                continue

            all_results.append(extracted.get("records", []))

            # better check this hardcoded version , it may be useless with current architecture
            if "analog.com" in url and extracted.get("page_count") is None and extracted.get("records") and extracted.get("total_records_declared"):
                extracted["page_count"] = math.ceil(extracted["total_records_declared"]/len(extracted["records"]))
                extracted["paging_needed"] = bool(extracted["page_count"] > 1)

            has_working_pagination = bool(
                resolved_pagination
                and resolved_pagination.get("confidence") != "none"
                and resolved_pagination.get("next_button_script")
            )

            if extracted.get("paging_needed") or has_working_pagination:
                page_count = extracted.get("page_count")
                identifier_value = page_data.get("identifier_value") if isinstance(page_data, dict) else None
                exclusive_value = page_data.get("exclusive_value") if isinstance(page_data, dict) else None
                new_html_pages = self.firecrawl.fetch_with_pagination(url, filtered_data.get("resolved_scripts"), resolved_pagination, page_count, identifier_value, exclusive_value)

                for number, page_html in enumerate(new_html_pages, start=2):
                    extracted_more = self.extractor.extract(page_html, user_prompt, available_fields)
                    self._count_unread(extracted_more, f"paginated page {number}")
                    all_results.append(extracted_more.get("records", []))

        # not one value got through - that is the whole catalog waiting to be scraped, so stop
        if filters_applied and self.unfiltered_pages and not all_results:
            raise ValueError(
                f"The filter had no effect on any of the {self.unfiltered_pages} value(s) - the filtered page "
                f"still reports {unfiltered_total} records, same as the unfiltered page. "
                f"Stopping before scraping the whole catalog."
            )

        return all_results


    def _count_unread(self, extracted: dict, where: str) -> None:
        """An unanswered page looks exactly like an empty one - say so, or a partial run passes as complete."""
        if not extracted.get("unavailable"):
            return

        self.unread_pages += 1
        print(f"WARNING: {where} could not be read - the model gave up on it, its records are missing from the result")

    def _is_distinctive_value(self, value: str) -> bool:
        """
        Worth searching the HTML for: "TLMG1100" pins down its own option, while "6" or
        "3.0A" matches half the page and would crowd out the windows that matter.
        """
        value = (value or "").strip()

        if len(value) < 4:
            return False

        # a number with an optional short unit is not distinctive, however long it is
        if re.fullmatch(r"[\d\s.,%/+\-–—<>=]+[a-zA-Z\"'°µΩ]{0,3}", value):
            return False

        return True

    def _dedupe_records(self, records: list[dict], key: str = "MPN") -> list[dict]:
        """
        Last safety net before export: drops repeated records.
        """
        deduped = []
        seen = set()

        for record in records:
            if not isinstance(record, dict):
                deduped.append(record)
                continue

            value = record.get(key)

            # the model returns the identifier as a link often enough, and a dict cannot go into a set
            if isinstance(value, (dict, list)):
                value = json.dumps(value, sort_keys=True, ensure_ascii=False)

            signature = value if value else json.dumps(record, sort_keys=True, ensure_ascii=False)

            if signature in seen:
                continue

            seen.add(signature)
            deduped.append(record)

        return deduped


    def resolve_csv(self, source: str | None = None) -> list[str]:
        """
        Turns a CSV/TXT/Excel file into a flat list of string values.
        If source is None, opens a file picker dialog for the user (falls back to
        a manual path prompt if no GUI is available, e.g. on a headless server).
        """
        if source is None:
            try:
                import tkinter as tk
                from tkinter import filedialog

                root = tk.Tk()
                root.withdraw()
                source = filedialog.askopenfilename(
                    title="Choose file with list of values",
                    filetypes=[
                        ("CSV / text", "*.csv *.txt"),
                        ("Excel", "*.xlsx *.xls"),
                        ("All files", "*.*"),
                    ],
                )
                root.destroy()
            except Exception:
                print("Failed to open file selection dialog, please enter the path manually.")
                source = None

            if not source:
                source = input("Enter the path to the file: ").strip()

            if not source:
                return []

        ext = os.path.splitext(source)[1].lower()

        if ext in (".csv", ".txt"):
            with open(source, encoding="utf-8") as f:
                content = f.read()
            items = [x.strip() for x in re.split(r"[,;\n]", content) if x.strip()]

        elif ext in (".xlsx", ".xls"):
            import openpyxl
            wb = openpyxl.load_workbook(source, data_only=True)
            ws = wb.active
            items = []
            for row in ws.iter_rows():
                for cell in row:
                    if cell.value is not None and str(cell.value).strip():
                        items.append(str(cell.value).strip())

        else:
            raise ValueError(f"Unsupported file format: '{ext}'. Use .csv, .txt, .xlsx or .xls.")

        return items

    #*THIS FUNC IS WRITTEN BY AI*
    def _print_sample(self, page_sample_result: dict):
        
        print("\n" + "=" * 60)
        print("PAGE PREVIEW")
        print("=" * 60)

        summary = page_sample_result.get("non_dev_summary")
        if summary:
            print(f"\n{summary}\n")

        fields = page_sample_result.get("available_fields")
        if fields:
            print("Available fields:")
            for f in fields:
                print(f"  - {f}")

        filter_options = page_sample_result.get("filter_options")
        if filter_options:
            print("\nFields that can be filtered by:")
            for opt in filter_options:
                print(f"  - {opt.get('name')}")

        print("=" * 60 + "\n")


    def normalize_filters(self, filters: list[dict] | None = None, user_filter_choice: list | None = None, user_csv_list: list | None = None) -> list[dict]:
        """
        Accept both shapes during the migration:
        1) new multi-filter format:
            [{"name": "Product number", "hints": [...], "values": [...]}, ...]
        2) legacy single-filter format:
            user_filter_choice = ["Product number", "part number"]
            user_csv_list = ["TMP75", "TMP117"]
        """
        if filters is not None:
            normalized = []
            for item in filters:
                if not isinstance(item, dict):
                    continue

                normalized.append({
                    "name": item.get("name", ""),
                    "hints": list(item.get("hints") or []),
                    "values": list(item.get("values") or []),
                })
            return normalized

        if user_filter_choice is None and user_csv_list is None:
            return []

        name = user_filter_choice[0] if user_filter_choice else ""
        hints = list(user_filter_choice[1:]) if user_filter_choice else []
        values = list(user_csv_list) if user_csv_list else []

        return [{"name": name, "hints": hints, "values": values}] if name else []

    def run(self):
        """
        Interactive entrypoint - the whole program lives here.
        """
        print("=== Product data downloader tool ===\n")

        url = input("Enter the page URL: ").strip()

        print("\nAnalyzing the page, one moment...")
        try:
            page_sample = self.page_sample(url)
        except RuntimeError as error:
            print(f"\n{error}\n")
            return

        self._print_sample(page_sample["page_sample_result"])

        try:
            filters_count = int(input("\nHow many filters do you want to set? [1]: ").strip() or "1")
        except ValueError:
            filters_count = 1
        filters_count = max(1, filters_count)

        filters = []
        for i in range(filters_count):
            print(f"\n--- Filter {i + 1}/{filters_count} ---")
            filter_name = input("Filter field name (e.g. 'Product number'): ").strip()
            if not filter_name:
                print("Empty name - skipping this filter.")
                continue

            hint1 = input("Optional helper word 1 (press Enter to skip): ").strip()
            hint2 = input("Optional helper word 2 (press Enter to skip): ").strip()
            hints = [h for h in (hint1, hint2) if h]

            if i == 0:
                print("How do you want to provide values for this filter?")
                print("  1. Type manually (values separated by commas ',', semicolons ';')")
                print("  2. Choose a file from computer (CSV / TXT / Excel)")
                choice = input("Choose 1 or 2: ").strip()
                if choice == "2":
                    values = self.resolve_csv()
                else:
                    raw = input("Enter values separated by commas/semicolons: ")
                    values = [x.strip() for x in re.split(r"[,;]", raw) if x.strip()]
            else:
                raw = input("Enter values separated by commas/semicolons: ")
                values = [x.strip() for x in re.split(r"[,;]", raw) if x.strip()]

            if not values:
                print("No values provided for this filter - skipping it.")
                continue

            print(f"Accepted {len(values)} values (first 20): {values[:20]}")
            filters.append({"name": filter_name, "hints": hints, "values": values})

        if not filters:
            print("No filters provided - aborting.")
            return

        print("\nWhat do you want to extract from each found product? (describe freely, e.g. 'number, price, package')")
        user_prompt = input("> ").strip()

        output_path = input("\nWhere to save the result? [Enter = data/excel/output.xlsx]: ").strip()
        if not output_path:
            output_path = "data/excel/output.xlsx"

        # a typo in the extension makes a file that no spreadsheet app will open
        if not output_path.lower().endswith((".xlsx", ".xlsm")):
            output_path = str(Path(output_path).with_suffix(".xlsx"))
            print(f"The result has to be an .xlsx file, saving as: {output_path}")

        pdf_choice = input("Download PDF datasheets as well? (y/n) [no in default]: ").strip().lower()
        download_pdfs = pdf_choice in ("t", "tak", "y", "yes")

        # the downloader looks the records up by these two fields, so the model has to be asked for
        # them before it extracts anything - afterwards the prompt is no longer read
        if download_pdfs and ("Full URL of the PDF datasheet" not in user_prompt or "MPN" not in user_prompt):
            user_prompt += (
                "\nRemember to extract these fields:\n"
                " - Full URL of the PDF datasheet\n"
                " - MPN\n"
                "So I can download the PDFs."
            )

        print("\nDownloading data, this may take a moment...\n")

        normalized_filters = self.normalize_filters(filters=filters)

        resolved_pagination = self.resolve_pagination(url, page_sample["page_sample_result"], page_sample["html_to_analize"])
        available_fields = page_sample["page_sample_result"].get("available_fields", [])

        filtered_data = self.filtered_scraping(
            url,
            page_sample["page_sample_result"],
            normalized_filters,
            html=page_sample["html_to_analize"],
        )

        extracted_data = self.extract_data(
            url,
            filtered_data,
            user_prompt,
            resolved_pagination,
            available_fields,
            page_sample["page_sample_result"].get("total_records_declared"),
        )

        flat_all_results = [record for sublist in extracted_data for record in sublist]
        flat_all_results = self._dedupe_records(flat_all_results)

        if not flat_all_results:
            print("No records to save - check the filter/field/values and try again.")
            return

        self.excel.save(flat_all_results, output_path)

        if self.unread_pages:
            print(f"WARNING: the result is incomplete - {self.unread_pages} page(s) could not be read, "
                  f"their records are missing. Re-run when the model is available again.")

        if self.unfiltered_pages:
            print(f"WARNING: the result is incomplete - the filter did not take effect for "
                  f"{self.unfiltered_pages} value(s), so their records are missing.")

        print(f"Saved {len(flat_all_results)} records to: {Path(output_path).resolve()}")

        if download_pdfs:
            print("Downloading PDF datasheets...")
            datasheet_dir = Path("data/datasheets")
            downloaded = self.pdf.download_all(flat_all_results, "Full URL of the PDF datasheet", "MPN", str(datasheet_dir))
            saved = [entry for entry in downloaded if entry.get("path")]
            print(f"Downloaded {len(saved)} of {len(downloaded)} datasheets to: {datasheet_dir.resolve()}")

        return {
            "output_path": output_path,
            "record_count": len(flat_all_results),
            "unread_pages": self.unread_pages,
            "unfiltered_pages": self.unfiltered_pages,
            "records": flat_all_results
        }


    

    def run_debug(self, url: str, user_prompt: str, user_filter_choice: list, user_csv_list: list, output_path: str, download_pdfs: bool = False, filters: list[dict] | None = None):

        """
        Almost same as run(), but with debug prints and timing at every stage - use during
        development/testing to see what's happening at each step.
        """

        # the action dumps and the model chatter are wanted here, and nowhere else
        self.debug = True
        self.firecrawl.debug = True
        self.extractor.debug = True
        start_test = time.time()
        print("DEBUG: Start of the test")

        start_sample = time.time()
        try:
            page_sample = self.page_sample(url)
        except RuntimeError as error:
            print(f"\n{error}\n")
            return

        print(f"page_sample: \n{page_sample['page_sample_result']}\n")

        # so we can grep the analysed HTML when a filter "isn't there" - tells apart
        # "not in the fetched HTML" from "in the HTML but the windows missed it"
        Path("data/shit").mkdir(parents=True, exist_ok=True)

        with open("data/shit/html_to_analize.txt", "w", encoding="utf-8") as f:
            f.write(page_sample["html_to_analize"] or "")
        print(f"DEBUG: html_to_analize length: {len(page_sample['html_to_analize'] or '')}\n")

        end_sample = time.time()

        start_analisys = time.time()
        normalized_filters = self.normalize_filters(filters=filters, user_filter_choice=user_filter_choice, user_csv_list=user_csv_list)

        resolved_pagination = self.resolve_pagination(url, page_sample["page_sample_result"], page_sample["html_to_analize"])
        available_fields = page_sample["page_sample_result"].get("available_fields", [])

        print(f"resolved_pagination: {resolved_pagination} \n")
        print(f"normalized_filters: {normalized_filters}\n")
        end_analisys = time.time()

        start_filter = time.time()
        print(f"filtering the data ...\n")

        filtered_data = self.filtered_scraping(
            url,
            page_sample["page_sample_result"],
            normalized_filters,
            html=page_sample["html_to_analize"],
        )
        end_filter = time.time()

        start_extract = time.time()
        print(f"extracting the data ...\n")
        extracted_data = self.extract_data(
            url,
            filtered_data,
            user_prompt,
            resolved_pagination,
            available_fields,
            page_sample["page_sample_result"].get("total_records_declared"),
        )
        end_extract = time.time()

        flat_all_results = [record for sublist in extracted_data for record in sublist]

        before_dedupe = len(flat_all_results)
        flat_all_results = self._dedupe_records(flat_all_results)
        print(f"flat_all_results (length): {len(flat_all_results)} (dropped {before_dedupe - len(flat_all_results)} duplicates)\n")

        if self.unread_pages:
            print(f"WARNING: the result is incomplete - {self.unread_pages} page(s) could not be read, "
                  f"their records are missing. Re-run when the model is available again.\n")

        if self.unfiltered_pages:
            print(f"WARNING: the result is incomplete - the filter did not take effect for "
                  f"{self.unfiltered_pages} value(s), so their records are missing.\n")

        if not flat_all_results:
            raise ValueError("No records to save - check the filter/field/URL")

        self.excel.save(flat_all_results, output_path)

        if download_pdfs:
            self.pdf.download_all(flat_all_results, "Full URL of the PDF datasheet", "MPN")
        print(f"check {output_path} path where the new output excel is\n")
        end_test = time.time()

        prompt_tokens = sum(t["prompt_tokens"] for t in self.extractor.tokens_usage)
        output_tokens = sum(t["output_tokens"] for t in self.extractor.tokens_usage)
        total_tokens = sum(t["total_tokens"] for t in self.extractor.tokens_usage)

        print("\n===== DEBUG: TOKEN USAGE =====")
        print("\n=== GEMINI ===")
        print(f"Prompt tokens : {prompt_tokens:,}")
        print(f"Output tokens : {output_tokens:,}")
        print(f"Total tokens  : {total_tokens:,}")

        print(f"\nDEBUG: test time: {end_test - start_test}")
        print(f"DEBUG: suggestion time: {end_sample - start_sample:.2f}s")
        print(f"DEBUG: analisys time: {end_analisys - start_analisys:.2f}s")
        print(f"DEBUG: filtering data time: {end_filter - start_filter:.2f}s")
        print(f"DEBUG: extracting data time: {end_extract - start_extract:.2f}s")

        return {
            "output_path": output_path,
            "record_count": len(flat_all_results),
            "unread_pages": self.unread_pages,
            "unfiltered_pages": self.unfiltered_pages,
            "records": flat_all_results
        }