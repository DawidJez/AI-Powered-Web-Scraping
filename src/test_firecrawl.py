import re
from firecrawl_client import FirecrawlClient


def main():
    client = FirecrawlClient()

    pages_data = client.fetch_all_pages(
        "https://www.ti.com/product-category/sensors/products.html",
        r'gpn="([A-Z0-9\-]+)"'
    )

    expected_per_page = 50

    all_mpns_md = set()
    all_mpns_gpn = set()

    pattern_md = re.compile(
        r'\[([A-Z0-9\-]+)\]\(https://www\.ti\.com/product/([A-Z0-9\-]+)\)'
    )
    pattern_gpn = re.compile(r'gpn="([A-Z0-9\-]+)"')

    for page_number, page_data in enumerate(pages_data, start=1):
        markdown = page_data["markdown"]
        html = page_data["html"]

        print("=" * 60)
        print(f"PAGE {page_number}")
        print("=" * 60)

        print(f"Markdown length: {len(markdown)} chars")
        print(f"HTML length: {len(html)} chars\n")

        # Zapis markdown każdej strony
        with open(f"debug_markdown_page_{page_number}.md", "w", encoding="utf-8") as f:
            f.write(markdown)

        # --- Markdown ---
        matches_md = pattern_md.findall(markdown)
        unique_mpns_md = {m[1] for m in matches_md}
        all_mpns_md.update(unique_mpns_md)

        print(f"[Markdown] Matches: {len(matches_md)}")
        print(f"[Markdown] Unique MPNs: {len(unique_mpns_md)}")

        # --- HTML gpn ---
        matches_gpn = pattern_gpn.findall(html)
        unique_mpns_gpn = set(matches_gpn)
        all_mpns_gpn.update(unique_mpns_gpn)

        print(f"[HTML gpn] Matches: {len(matches_gpn)}")
        print(f"[HTML gpn] Unique MPNs: {len(unique_mpns_gpn)}")

        if len(unique_mpns_gpn) <= expected_per_page:
            print("Likely one paginated page.")
        else:
            print("More than one page worth of products.")

        print()

    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)

    print(f"Pages fetched: {len(pages_data)}")
    print(f"Unique MPNs from Markdown : {len(all_mpns_md)}")
    print(f"Unique MPNs from HTML gpn : {len(all_mpns_gpn)}")

    print("\nFirst 20 MPNs:")
    print(sorted(all_mpns_gpn)[:20])


if __name__ == "__main__":
    main()