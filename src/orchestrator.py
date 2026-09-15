import math
import time
import json
from pathlib import Path
import re
import os
from itertools import product

from ai_extractor import AIExtractor
from firecrawl_client import FirecrawlClient
from excel_exporter import ExcelExporter
from pdf_downloader import PDFDownloader

class Orchestrator:
    def __init__(self):
        self.extractor = AIExtractor()
        self.firecrawl = FirecrawlClient()
        self.excel = ExcelExporter()
        self.pdf = PDFDownloader()

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

    def resolve_scripts(self, url: str, analisys: dict, filter: dict, max_depth: int = 5) -> list:        
        if analisys.get("confidence") == "none":
            raise ValueError(f"DEBUG: '{filter['name']}' not found in HTML, evidence: {analisys.get('evidence')}")

        # ti has this problem with disabled inputs with "Filter Input"
        if "www.ti.com" in url:
            
            known_disabled_pattern = "Filter Input"

            if analisys.get("target_script") and known_disabled_pattern in analisys.get("target_script"):
                
                analisys["target_script"] = None
                analisys["interaction_type"] = "unknown"
                analisys["gate_needed"] = True
        
        if analisys["gate_script"] and not analisys.get("target_script"):
            scripts = [analisys["gate_script"]]
            last_result = None # out target script

            for _ in range(1,max_depth):
                actions = []

                for s in scripts:
                    actions.append({"type": "executeJavascript", "script": s})
                    actions.append({"type": "wait", "milliseconds": 800})

                data_to_search = self.firecrawl.fetch_with_given_actions(url, actions) # we have to go deeper

                result = self.extractor.find_script(scripts, filter, data_to_search["html"], apply_script=analisys.get("apply_script"))

                # ti has this problem with disabled inputs with "Filter Input"
                if "www.ti.com" in url:
                    
                    known_disabled_pattern = "Filter Input"

                    if result.get("script") and known_disabled_pattern in result.get("script"):
                        
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

                if not result.get("needs_more_depth", False):
                    break

            final_script = last_result.get("script") if last_result else None
            gate_scripts = scripts[:-1] if final_script and scripts and scripts[-1] == final_script else scripts
            return {                                                                                  
                "gate_scripts": gate_scripts,                                                         
                "final_script": final_script,
                "available_options": last_result.get("available_options") if last_result else [],
                "interaction_type": last_result.get("interaction_type") if last_result else "unknown",
                "apply_script": result.get("apply_script")
            }                                                                                  
        
        gate_scripts = [analisys["gate_script"]] if analisys.get("gate_script") else []
        return {
            "gate_scripts": gate_scripts,
            "final_script": analisys.get("target_script"),
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

        if not normalized_filters:
            raise ValueError("No valid filters were provided for scraping")

        filter_options = page_sample_result.get("filter_options", [])
        available_fields = page_sample_result.get("available_fields", [])
        identifier_hints = ["number", "id", "code", "part", "sku", "mpn", "name"]

        resolved_filters = []
        known_apply_script = None

        for filter_def in normalized_filters:
            name = (filter_def.get("name") or "").strip()
            hints = list(filter_def.get("hints") or [])
            values = list(filter_def.get("values") or [])
            if not name:
                continue

            search_terms = [name] + [h for h in hints if h]

            matched_filter = next((f for f in filter_options if name.lower() in f["name"].lower()), None)
            filter = matched_filter

            if matched_filter is None:
                matched_filter = next((f for f in available_fields if name.lower() in f.lower()), None)

                if matched_filter is None:
                    raise ValueError(f"Filter '{name}' not found in detected filter_options")

                matched_filter_lc = matched_filter.lower()
                if any(hint in matched_filter_lc for hint in identifier_hints):
                    filter = {"name": matched_filter, "cardinality": "identifier", "example_values": None}
                else:
                    filter = {"name": matched_filter, "cardinality": "category", "example_values": None}

            filter["search_terms"] = search_terms

            analisys = self.search_for_filters(html, search_terms)

            # apply_script is one global button for the whole filter panel - once any filter's
            # analysis finds it, feed it forward so later filters don't need to rediscover it
            if not analisys.get("apply_script") and known_apply_script:
                analisys["apply_script"] = known_apply_script

            resolved = self.resolve_scripts(url, analisys, filter)

            if not known_apply_script and resolved.get("apply_script"):
                known_apply_script = resolved["apply_script"]

            resolved_filters.append({
                "resolved": resolved,
                "name": name,
                "values": values,
                "hints": hints,
            })

        if not resolved_filters:
            raise ValueError("No valid filters resolved for scraping")
        print(f"DEBUG: resolved_filters = {resolved_filters}")
        #return 0
        
        return {
            "filtered_data": self.firecrawl.fetch_filtered_data(url, resolved_filters),
            "resolved_scripts": resolved_filters,
            "apply_script": known_apply_script,
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

        resolved_pagination = self.extractor.resolve_pagination(page_sample, html, url)
        if "analog.com" in url:
            resolved_pagination = {
                "next_button_script": '(function() { document.querySelector(\'button[aria-label="Next page"]\').click(); })();',
                "disabled_check_script": '(function() { const btn = document.querySelector(\'button[aria-label="Next page"]\'); return !!(btn && btn.disabled); })();',
                "confidence": "high",
                "evidence": "hardcoded - virtualized grid, pagination not visible in static HTML/markdown"
            }

        if resolved_pagination.get("confidence") != "none" and resolved_pagination.get("next_button_script"):
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
        ):
        if available_fields is None:
            available_fields = []

        all_results = []
        data = filtered_data.get("filtered_data")
        
        for page_data in data:
            extracted = self.extractor.extract(page_data, user_prompt, available_fields)
            all_results.append(extracted.get("records", []))

            if "analog.com" in url and extracted.get("page_count") is None and extracted.get("records") and extracted.get("total_records_declared"):
                extracted["page_count"] = math.ceil(extracted["total_records_declared"]/len(extracted["records"]))
                extracted["paging_needed"] = bool(extracted["page_count"] > 1)

            if extracted.get("paging_needed"):
                page_count = extracted["page_count"]
                identifier_value = page_data.get("identifier_value") if isinstance(page_data, dict) else None
                new_html_pages = self.firecrawl.fetch_with_pagination(url, filtered_data.get("resolved_scripts"), resolved_pagination, page_count, identifier_value)

                for page_html in new_html_pages:
                    extracted_more = self.extractor.extract(page_html, user_prompt, available_fields)
                    all_results.append(extracted_more.get("records", []))

        return all_results

    
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


    def resolve_filters(self, html: str, filters: list[dict]) -> list[dict]:
        """
        Resolve each filter independently using the existing AI logic.
        This remains compatible with the old single-filter call path.
        """
        resolved = []
        for filter_def in filters:
            name = filter_def.get("name", "").strip()
            hints = list(filter_def.get("hints") or [])
            search_terms = [name] + [h for h in hints if h]
            if not name:
                continue
            resolved.append(self.search_for_filters(html, search_terms))
        return resolved



    def run(self):
        """
        Interactive entrypoint - the whole program lives here.
        """
        print("=== Product data downloader tool ===\n")

        url = input("Enter the page URL: ").strip()

        print("\nAnalyzing the page, one moment...")
        page_sample = self.page_sample(url)
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

        pdf_choice = input("Download PDF datasheets as well? (y/n) [no in default]: ").strip().lower()
        download_pdfs = pdf_choice in ("t", "tak", "y", "yes")

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
        )

        flat_all_results = [record for sublist in extracted_data for record in sublist]

        if not flat_all_results:
            print("No records to save - check the filter/field/values and try again.")
            return

        self.excel.save(flat_all_results, output_path)
        print(f"Saved {len(flat_all_results)} records to: {output_path}")

        if download_pdfs and ("Full URL of the PDF datasheet" not in user_prompt or "MPN" not in user_prompt):
            user_prompt += (
                "\nRemember to extract these fields:\n"
                " - Full URL of the PDF datasheet\n"
                " - MPN\n"
                "So I can download the PDFs."
            )

        if download_pdfs:
            print("Downloading PDF datasheets...")
            self.pdf.download_all(flat_all_results, "Full URL of the PDF datasheet", "MPN")
            print("PDF download completed.")

        return {
            "output_path": output_path,
            "record_count": len(flat_all_results),
            "records": flat_all_results
        }


    

    def run_debug(self, url: str, user_prompt: str, user_filter_choice: list, user_csv_list: list, output_path: str, download_pdfs: bool = False, filters: list[dict] | None = None):
        """
        Almost same as run(), but with debug prints and timing at every stage - use during
        development/testing to see what's happening at each step.
        """
        start_test = time.time()
        print("DEBUG: Start of the test")

        start_sample = time.time()
        page_sample = self.page_sample(url)
        print(f"page_sample: \n{page_sample['page_sample_result']}\n")
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
        )
        end_extract = time.time()

        flat_all_results = [record for sublist in extracted_data for record in sublist]
        print(f"flat_all_results (length): {len(flat_all_results)}\n")

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
            "records": flat_all_results
        }