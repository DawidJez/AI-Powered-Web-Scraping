import math
import time
import json
from pathlib import Path
import re

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
        """ Fetchers a single/first page from url and asks AI 
        what is possible to extract from it. Returns a list of field names 
        and analized html content required to filtered scraping. """

        page = self.firecrawl.fetch_page(url)
        result = {
            "page_sample_result": self.extractor.suggestion(page["markdown"]),
            "html_to_analize": page["html"]
        }

        return result
    
    def search_for_filters(self, html: str, search_term: str):
        """ Analizes html content and returns a list of filters found on the page. """
        result = self.extractor.analize_html(html, search_term)

        return result

    def resolve_scripts(self, url: str, analisys: dict, filter: dict, max_depth: int = 5) -> list:        
        if analisys.get("confidence") == "none":
            raise ValueError(f"DEBUG: nie znaleziono '{filter['name']}' w HTML, evidence: {analisys.get('evidence')}")

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

                #import time
                #with open(f"data/test_files/debug_full_html_{int(time.time()*1000)}.txt", "w", encoding="utf-8") as f:
                #   f.write(data_to_search["html"])

                result = self.extractor.find_script(scripts, filter, data_to_search["html"])

                # ti has this problem with disabled inputs with "Filter Input"
                if "www.ti.com" in url:
                    
                    known_disabled_pattern = "Filter Input"

                    if result.get("script") and known_disabled_pattern in result.get("script"):
                        
                        result["script"] = None
                        result["control_type"] = "none"
                        result["needs_more_depth"] = True

                last_result = result

                if result["needs_more_depth"] == False and result.get("control_type") == "none":
                    print("DEBUG: AI nie znalazlo nic, przerywam petle")
                    break

                candidate = result.get("script")

                # category-type field (available_options) - script may be null, but it doesn't mean there is nothing
                if candidate:    
                    if candidate in scripts:
                        print(f"DEBUG: AI zwrocilo ten sam script co juz mamy ('{candidate}') - utkneliśmy, przerywam")
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
                "interaction_type": last_result.get("interaction_type") if last_result else "unknown"
            }                                                                                  
        
        gate_scripts = [analisys["gate_script"]] if analisys.get("gate_script") else []
        return {
            "gate_scripts": gate_scripts,
            "final_script": analisys.get("target_script"),
            "available_options": analisys.get("available_options"),
            "interaction_type": analisys.get("interaction_type")
        }

    def filtered_scraping(self, url: str, analisys: dict, page_sample_result: dict, csv_list: list[str], user_choices: list) -> dict:
        """For now, we give this function user_choices as user choice of filter + help phrases for html extract"""
        user_choice = user_choices[0]
       
        filter_options = page_sample_result.get("filter_options",[])
        available_fields = page_sample_result.get("available_fields", [])

        matched_filter = next((f for f in filter_options if user_choice.lower() in f["name"].lower()), None)
        filter = matched_filter

        if matched_filter is None:
            matched_filter = next((f for f in available_fields if user_choice.lower() in f.lower()), None) # dict -> str

            if matched_filter is None:
                raise ValueError(f"Filter '{user_choice}' not found in detected filter_options")
            
            identifier_hints = ["number", "id", "code", "part", "sku", "mpn", "name"]
            matched_filter_lc = matched_filter.lower()

            if any(hint in matched_filter_lc for hint in identifier_hints):
                filter = {
                    "name": matched_filter,
                    "cardinality": "identifier",
                    "example_values": None
                }
            else:
                filter = {
                    "name": matched_filter,
                    "cardinality": "category",
                    "example_values": None
                }

        filter["search_terms"] = [choice for choice in user_choices]
        resolved = self.resolve_scripts(url, analisys, filter)

        print(f"\n\n\n{resolved}\n\n\n")

        filtered_data = self.firecrawl.fetch_filtered_data(url, resolved, filter, csv_list, resolved["available_options"])

        return {
            "filtered_data": filtered_data,
            "resolved_scripts": resolved
            }

    def resolve_pagination(self, url: str, page_sample: dict, html: str) -> dict:
        safe_url = re.sub(r"[^a-zA-Z0-9_-]", "_", url) # sanitize url to create a valid filename
        filename = Path(f"data/resolved_pagination/resolved_pagination_{safe_url}.json")
        
        if filename.exists():
            age_seconds = time.time() - filename.stat().st_mtime

            if age_seconds < 86400: # 24h
                cached = json.loads(filename.read_text(encoding="utf-8"))
                print(f"cached: {cached}")

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
            
        print(f"\nresolved_pagination: {resolved_pagination}")

        if resolved_pagination.get("confidence") != "none" and resolved_pagination.get("next_button_script"):
            with open(filename, "w", encoding="utf-8") as f:
                f.write(json.dumps(resolved_pagination))

        return resolved_pagination

    def extract_data(
            self, 
            url: str, 
            filtered_data: dict, 
            user_prompt: str, 
            csv_list: list, 
            resolved_pagination: dict
        ):
        all_results = []

        data = filtered_data["filtered_data"]
        scripts = filtered_data["resolved_scripts"]

        for item, page_data in zip(csv_list, data):
            extracted = self.extractor.extract(page_data, user_prompt)
            print(f"DEBUG: page1 dla item={item}: len(records)={len(extracted['records'])}, page_count={extracted.get('page_count')}")

            all_results.append(extracted["records"])

            if "analog.com" in url and extracted.get("page_count") == None and extracted.get("records") and extracted.get("total_records_declared"):
                extracted["page_count"] = math.ceil(extracted["total_records_declared"]/len(extracted["records"]))
                extracted["paging_needed"] = bool(extracted["page_count"] > 1)

            print(f"extracted.get('paging_needed'): {extracted.get('paging_needed')}")
            if extracted.get("paging_needed"):
                page_count = extracted["page_count"]
                new_html_pages = self.firecrawl.fetch_with_pagination(url, scripts, resolved_pagination, page_count, item)

                for page_html in new_html_pages:
                    extracted_more = self.extractor.extract(page_html, user_prompt)
                    print(f"DEBUG: next page dla item={item}: len(records)={len(extracted_more['records'])}")
                    all_results.append(extracted_more["records"])

        return all_results

    def test_filter_finder(self):
        url = "https://www.ti.com/product-category/sensors/products.html"

        import json

        with open("data/page_sample_result.json", "r", encoding="utf-8") as f:
            page_sample_result = json.load(f)
        
        with open("data/html_to_analize.txt", "r", encoding="utf-8") as f:
            html_to_analize = f.read()

        user_filter_choice = "Product number"
        user_csv_list = ["TMP75","TMP117","TMP275"]
        user_csv_list = ["TMP75"]

        filter_options = page_sample_result.get("filter_options", [])
        matched_filter = next((f for f in filter_options if user_filter_choice in f["name"]), None)

        matched_filter["search_terms"] = ["Product number", "part number"]

        if not matched_filter:
            raise ValueError(f"DEBUG: nie znaleziono filtra pasującego do '{user_filter_choice}' w filter_options")

        
        filter_analisys = self.search_for_filters(html_to_analize, user_filter_choice)
        print(f"filter_analisys: \n {filter_analisys} \n\n")

        resolved_scripts = self.resolve_scripts(url, filter_analisys, matched_filter)
        print(f"resolved_scripts: \n {resolved_scripts}")


    import os
import re
import json


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
                title="Wybierz plik z listą wartości",
                filetypes=[
                    ("CSV / tekst", "*.csv *.txt"),
                    ("Excel", "*.xlsx *.xls"),
                    ("Wszystkie pliki", "*.*"),
                ],
            )
            root.destroy()
        except Exception:
            print("DEBUG: nie udało się otworzyć okna wyboru pliku, wpisz ścieżkę ręcznie.")
            source = None

        if not source:
            source = input("Podaj ścieżkę do pliku: ").strip()

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
        raise ValueError(f"Nieobsługiwany format pliku: '{ext}'. Użyj .csv, .txt, .xlsx lub .xls.")

    return items


def _print_sample(self, page_sample_result: dict):
    """Readable, non-technical summary of what the probe found on the page."""
    print("\n" + "=" * 60)
    print("PODGLĄD STRONY")
    print("=" * 60)

    summary = page_sample_result.get("non_dev_summary")
    if summary:
        print(f"\n{summary}\n")

    fields = page_sample_result.get("available_fields")
    if fields:
        print("Dostępne pola:")
        for f in fields:
            print(f"  - {f}")

    filter_options = page_sample_result.get("filter_options")
    if filter_options:
        print("\nPola, po których można filtrować:")
        for opt in filter_options:
            print(f"  - {opt.get('name')}")

    print("=" * 60 + "\n")


    def run(self):
        """
        Interactive entrypoint - the whole program lives here. Walks the user
        through providing a URL, choosing what to search for, what fields to
        extract, and where to save the result.
        """
        print("=== Narzędzie do pobierania danych produktowych ===\n")

        url = input("Podaj adres URL strony: ").strip()

        print("\nAnalizuję stronę, chwila...")
        page_sample = self.page_sample(url)
        self._print_sample(page_sample["page_sample_result"])

        print("Jak chcesz podać listę wartości do wyszukania?")
        print("  1. Wpisać ręcznie (wartości oddzielone przecinkami)")
        print("  2. Wybrać plik z komputera (CSV / TXT / Excel)")
        choice = input("Wybierz 1 lub 2: ").strip()

        if choice == "2":
            user_csv_list = self.resolve_csv()
        else:
            raw = input("Wpisz wartości oddzielone przecinkami: ")
            user_csv_list = [x.strip() for x in raw.split(",") if x.strip()]

        if not user_csv_list:
            print("Nie podano żadnych wartości do wyszukania - przerywam.")
            return

        print(f"\nPrzyjęto {len(user_csv_list)} wartości: {user_csv_list}")

        filter_name = input("\nPodaj nazwę pola filtra, po którym szukać (np. 'Product number'): ").strip()
        hint1 = input("Opcjonalne słowo pomocnicze 1 (Enter aby pominąć): ").strip()
        hint2 = input("Opcjonalne słowo pomocnicze 2 (Enter aby pominąć): ").strip()
        user_filter_choice = [filter_name] + [h for h in (hint1, hint2) if h]

        print("\nCo chcesz wyciągnąć z każdego znalezionego produktu? (opisz swobodnie, np. 'numer, cena, obudowa')")
        user_prompt = input("> ").strip()

        output_path = input("\nGdzie zapisać wynik? [Enter = data/excel/output.xlsx]: ").strip()
        if not output_path:
            output_path = "data/excel/output.xlsx"

        pdf_choice = input("Czy pobrać też karty katalogowe PDF? (t/n) [n]: ").strip().lower()
        download_pdfs = pdf_choice in ("t", "tak", "y", "yes")

        print("\nPobieram dane, to może chwilę potrwać...\n")

        filter_analisys = self.search_for_filters(page_sample["html_to_analize"], user_filter_choice[0])
        resolved_pagination = self.resolve_pagination(url, page_sample["page_sample_result"], page_sample["html_to_analize"])

        filtered_data = self.filtered_scraping(
            url, filter_analisys,
            page_sample["page_sample_result"],
            user_csv_list,
            user_filter_choice
        )

        extracted_data = self.extract_data(
            url,
            filtered_data,
            user_prompt,
            user_csv_list,
            resolved_pagination
        )

        flat_all_results = [record for sublist in extracted_data for record in sublist]

        if not flat_all_results:
            print("Brak rekordów do zapisania - sprawdź filtr/pole/wartości i spróbuj ponownie.")
            return

        self.excel.save(flat_all_results, output_path)
        print(f"Zapisano {len(flat_all_results)} rekordów do: {output_path}")

        if download_pdfs:
            print("Pobieram karty katalogowe PDF...")
            self.pdf.download_all(flat_all_results, "Full URL of the PDF datasheet", "MPN")
            print("Pobieranie PDF zakończone.")

        return {
            "output_path": output_path,
            "record_count": len(flat_all_results),
            "records": flat_all_results
        }

    def run_debug(self, url: str, user_prompt: str, user_filter_choice: list, user_csv_list: list, output_path: str = "data/excel/output.xlsx", download_pdfs: bool = False):
        """
        Almost same as run() (), but with debug prints and timing at every stage - use during
        development/testing to see what's happening at each step.
        """
        start_test = time.time()
        print("DEBUG: Start of the test")

        start_sample = time.time()
        page_sample = self.page_sample(url)
        print(page_sample["page_sample_result"])
        end_sample = time.time()

        start_analisys = time.time()
        filter_analisys = self.search_for_filters(page_sample["html_to_analize"], user_filter_choice[0])
        resolved_pagination = self.resolve_pagination(url, page_sample["page_sample_result"], page_sample["html_to_analize"])
        print(filter_analisys)
        end_analisys = time.time()

        start_filter = time.time()
        filtered_data = self.filtered_scraping(
            url, filter_analisys,
            page_sample["page_sample_result"],
            user_csv_list,
            user_filter_choice
        )
        end_filter = time.time()

        start_extract = time.time()
        extracted_data = self.extract_data(
            url,
            filtered_data,
            user_prompt,
            user_csv_list,
            resolved_pagination
        )
        end_extract = time.time()

        flat_all_results = [record for sublist in extracted_data for record in sublist]

        if not flat_all_results:
            raise ValueError("Brak rekordów do zapisania - sprawdź filtr/pole/URL")

        self.excel.save(flat_all_results, output_path)

        if download_pdfs:
            self.pdf.download_all(flat_all_results, "Full URL of the PDF datasheet", "MPN")

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
        print(f"\nDEBUG: extracting data time: {end_extract - start_extract:.2f}s")

        return {
            "output_path": output_path,
            "record_count": len(flat_all_results),
            "records": flat_all_results
        }




    def test(self):
        ########################    ACTUAL TEST     #################################
        start_test = time.time()

        print("DEBUG: Start of the test")

        # url = "https://www.hebe.pl/wlosy/" -->Strong CloudFlare
        # url = "https://www.ti.com/product-category/rf-microwave/rf-power-detectors/products.html" works great
        url = "https://www.ti.com/product-category/sensors/products.html#" # --> shit cool
        # url = "https://www.analog.com/en/product-category/sensors.html" --> doesnt work that far away from target
        url = "https://www.analog.com/en/parametricsearch/2475#/"
        # url = "https://www.vishay.com/en/leds/smd/"
        #url = "https://www.infineon.com/products/battery-management-ics#products"
        url = "https://www.infineon.com/products/isolation/digital-isolators#products"

        # if pdf wanted always include "- Full URL of the PDF datasheet" and "- MPN" (as your producy/part) 
        # so pdf downloader would work
        user_prompt = """
            Extract every record from the page content. For each record, return:

            - MPN
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

            The page usually states how many records it contains (e.g. "1-50 of 128",
            "50 results"). Use that count as a checklist - your records array must
            contain exactly that many entries for this page. If your count comes up
            short, go back through the content again before responding.

            Use null for any field you cannot confidently determine - never guess.
        """
        user_prompt = """
            Extract every record from the page content.

            The page usually states how many records it contains (e.g. "1-50 of 128",
            "50 results"). Use that count as a checklist - your records array must
            contain exactly that many entries for this page. If your count comes up
            short, go back through the content again before responding.
        """

        start_sample = time.time()

        page_sample = self.page_sample(url)

        print(page_sample["page_sample_result"])
        # 1. is the filter name 2. and 3. are help phrases (max 2 help phrases)
        #user_choices = ["Pin count", "OR", "102"]
        #user_choices = ["Parts", "Filter Parts"]
        #user_choices = ["Part Number", "TLM"]
        #user_choices = ["Product number", "part number"]
        user_choices = ["Qualification", "Select", "Industrial"]
        user_filter_choice = user_choices[0]

        # user_csv_list = ["30", "24"]
        user_csv_list = ["TMP75","TMP117", "ISO", "TMP"]
        user_csv_list = [
            "4DIR0400H",
            "4DIR0400HA",
            "4DIR0401H",
            "4DIR0401HA",
            "4DIR1400H",
            "4DIR1400HA",
            "4DIR1401H",
            "4DIR1401HA",
            "4DIR1420H",
            "4DIR1420HA",
            "4DIR1421H",
            "4DIR1421HA"
        ]
        user_csv_list = ["Industrial"]
        #user_csv_list = ["TLMY1100"]
        #user_csv_list= ["TLE9012"]
        # user_csv_list = ["TLMG1100", "TLMO1000"]
        # user_csv_list = ["360"]
        # user_csv_list = ["102"]

        end_sample = time.time()
        start_analisys = time.time()

        filter_analisys = self.search_for_filters(page_sample["html_to_analize"], user_filter_choice)
        resolved_pagination = self.resolve_pagination(url, page_sample["page_sample_result"], page_sample["html_to_analize"])

        print(filter_analisys)

        end_analisys = time.time()
        start_filter = time.time()

        filtered_data = self.filtered_scraping(
            url, filter_analisys, 
            page_sample["page_sample_result"], 
            user_csv_list, 
            user_choices
        )

        end_filter = time.time()

        start_extract = time.time()
        extracted_data = self.extract_data(
            url,
            filtered_data,
            user_prompt,
            user_csv_list,
            resolved_pagination
        )

        end_extract = time.time()

        flat_all_results = [record for sublist in extracted_data for record in sublist]
        self.excel.save(flat_all_results, "data/excel/output.xlsx")
        # self.pdf.download_all(flat_all_results, "Full URL of the PDF datasheet", "MPN") -> takes a lot of time, so we skip it for now

        end_test = time.time()
        ########################    ACTUAL TEST     #################################
                
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
        print(f"\nDEBUG: extracting data time: {end_extract - start_extract:.2f}s")




    def test_stary(self):
        url = "https://www.vishay.com/en/mosfets/"
        url = "https://www.vishay.com/en/leds/smd/"
        #url = "https://www.ti.com/product-category/sensors/products.html"
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

        start_test = time.time()
        print("DEBUG: Start testu")

        #with open("data/test_files/test_page_sample.txt", "r", encoding="utf-8") as f:
        #    test_content = f.read()
        
        #result = {
        #    "markdown_result": "",
        #    "html_to_analize": test_content
        #}
        #"""
        start_sample = time.time()
        result = self.page_sample(url)
        end_sample = time.time()

        rezultat = result["page_sample_result"]
        only_filters = rezultat["available_fields"]
        only_products = rezultat["filter_options"]

        #with open("data/html_to_analize.txt", "w", encoding="utf-8") as f:
        #   f.write(result["html_to_analize"])

        #return 0

        print(f"DEBUG: sample page result = \n{only_filters, only_products}")
        # user_choice = input("Enter your choice: ").strip()

        #csv_list = ["TMP75","TMP117","TMP275"]
        user_choice = "Dominant Wavelength Min."
        csv_list = ["627"]

        start_analisys = time.time()
        analisys = self.search_for_filters(result["html_to_analize"], user_choice)
        end_analisys = time.time()
        #"""
        start_filter = time.time()
        filtered_data = self.filtered_scraping(url, analisys, result["page_sample_result"], csv_list, user_choice)
        ai_filtered_data = self.extractor.debug_ask_ai_about_products(filtered_data)
        #selector = "input[aria-label='Product number Filter Input']"
        #filter = {
        #    "name": "Product number",
        #    "cardinality": "identifier",
        #    "example_values": None
        #}
        #csv_list = ["TMP117"]
        #filtered_data = self.firecrawl.fetch_filtered_data(url, selector, filter, csv_list)
        end_filter= time.time()

        end_test = time.time()
        
        prompt_tokens = sum(t["prompt_tokens"] for t in self.extractor.tokens_usage)
        output_tokens = sum(t["output_tokens"] for t in self.extractor.tokens_usage)
        total_tokens = sum(t["total_tokens"] for t in self.extractor.tokens_usage)

        print("\n===== DEBUG: TOKEN USAGE =====")
        print("\n=== GEMINI ===")
        print(f"Prompt tokens : {prompt_tokens:,}")
        print(f"Output tokens : {output_tokens:,}")
        print(f"Total tokens  : {total_tokens:,}")
        
        print(f"DEBUG: test time: {end_test - start_test}")
        print(f"\nDEBUG: suggestion time: {end_sample - start_sample:.2f}s")
        print(f"\nDEBUG: analisys time: {end_analisys - start_analisys:.2f}s")
        print(f"\nDEBUG: filtering data time: {end_filter - start_filter:.2f}s")

        # print(f"\nDEBUG: result = \n{result}")
        #print(f"DEBUG: sample page result = \n{result["page_sample_result"]}")
        #print(f"DEBUG: analisys result = \n{analisys}")
        #print(f"DEBUG: filtered data result = \n{ai_filtered_data}")
        return 1