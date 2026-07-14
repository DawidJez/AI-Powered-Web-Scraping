from firecrawl_client import FirecrawlClient


def main():
    client = FirecrawlClient()

    pages = client.fetch_all_pages("https://www.ti.com/product-category/sensors/products.html")
    merged = client.merge_pages(pages)

    markdown = merged["markdown"]
    html = merged["html"]

    print("=" * 60)
    print("MERGED RESULT")
    print("=" * 60)
    print(f"Pages merged      : {len(pages)}")
    print(f"Markdown length   : {len(markdown):,} chars")
    print(f"HTML length       : {len(html):,} chars")

    with open("merged_markdown.md", "w", encoding="utf-8") as f:
        f.write(markdown)

    with open("merged_html.html", "w", encoding="utf-8") as f:
        f.write(html)

    print("\nSaved:")
    print(" - merged_markdown.md")
    print(" - merged_html.html")


if __name__ == "__main__":
    main()