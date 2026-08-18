from firecrawl_client import FirecrawlClient
import time
from ai_extractor import AIExtractor

PAGE_SEPARATOR = "=" * 50
OUTPUT_FILE = "data/downloaded_pages.md"
URL = "https://www.ti.com/product-category/sensors/products.html"


def save_pages(pages: list[dict], output_file: str = OUTPUT_FILE) -> None:
    """Writes a list of pages (markdown) into a single file, separated by a page-number separator."""
    print(f"\nWriting {len(pages)} pages to {output_file}...")

    with open(output_file, "w", encoding="utf-8") as f:
        for i, page in enumerate(pages):
            f.write(f"{PAGE_SEPARATOR}\n")
            f.write(f"PAGE {i + 1}\n")
            f.write(f"{PAGE_SEPARATOR}\n")
            f.write(page["markdown"])
            f.write("\n")

    print(f"Saved: {output_file}")


def load_pages(input_file: str = OUTPUT_FILE) -> list[str]:
    """Loads the file saved by save_pages() back as list[str] (markdown per page)."""
    with open(input_file, "r", encoding="utf-8") as f:
        content = f.read()

    raw_pages = content.split(f"{PAGE_SEPARATOR}\n")
    pages = []

    for chunk in raw_pages:
        chunk = chunk.strip()
        if not chunk:
            continue
        # usuń linię "PAGE N" z początku bloku
        lines = chunk.split("\n", 1)
        if lines[0].startswith("PAGE "):
            page_markdown = lines[1] if len(lines) > 1 else ""
        else:
            page_markdown = chunk
        pages.append(page_markdown.strip())

    return pages


def main():
    """
    print(f"Rozpoczynam pobieranie stron z: {URL}\n")

    client = FirecrawlClient()
    pages = client.fetch_all_pages(URL)

    print(f"\nDownloaded a total of {len(pages)} pages.")

    save_pages(pages)

    print("\nVerification - loading the file back...")
    loaded = load_pages()
    print(f"Loaded {len(loaded)} pages from file (should match the {len(pages)} downloaded).")
    """
    pages = load_pages()

    product_number = "TMP75"

    print("AI version status: START\n")
    start2 = time.time()
    
    extractor = AIExtractor()
    records = extractor.extract(
        pages,
        f"""
            Find every element on the page whose MPN (product number) starts with,
            or is a variant of, "{product_number}" - including the exact base part
            ("{product_number}") as well as any suffixed/derivative versions
            (e.g. automotive-qualified variants like "-Q1", grade/tolerance suffixes
            like a trailing letter, military/space-grade variants, or any other
            variant that shares "{product_number}" as its root/prefix).

            Do NOT limit results to only an exact match of "{product_number}" -
            include all such related variants found on the page.

            There may be zero, one, or multiple such elements on the page - for example,
            the same MPN could belong to different manufacturers, appear in different
            subcategories, or be rendered multiple times in the page's underlying structure
            (e.g. once as a summary/header entry and once as a detailed row).

            Do NOT assume there is only one correct match. Do NOT merge, filter, or discard
            any occurrence - return every single one you find as a separate object,
            even if some occurrences have mostly empty/null fields.
            Do NOT guess or fabricate values for missing fields.

            For each matching occurrence, extract the following fields:
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

            Return a JSON array containing one object per occurrence found.
            If no matches are found at all, return an empty array [].
        """,
        search_term=product_number
    )
    print(records)
    print("\n")

    end2 = time.time()
    time2 = (end2 - start2)
    print("AI version status: END")
    print(f"Time: {time2}")
    

if __name__ == "__main__":
    main()