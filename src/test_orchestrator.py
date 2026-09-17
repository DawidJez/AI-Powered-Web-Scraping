from orchestrator import Orchestrator

TI_PROMPT = """
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
"""

GENERIC_PROMPT = """
    Extract every record from the page content. For each record, return whatever
    fields are available (part number, description, package, price, status,
    datasheet URL, and any other relevant technical specs shown).
"""

TEST6_PROMPT = """
    Extract first 20 records from the page content. For each record, return whatever
    fields are available (part number, description, package, price, status,
    datasheet URL, and any other relevant technical specs shown).
"""

SAMTEC_PROMPT = """
    Extract everything from the page content about this product: CLE-123-01-G-DV-A
"""

AMPHENOL_PROMPT = """
    Extract everything from the page content about given products from redirection links.
"""

"""
Page sample -> probe of the page and analisys of the 1. page takes 50-60s
Filtering per one record from csv list takes 15-55s -> wide range of time comes from the DOM structure
Extracting data from markdown takes 20-70s -> also very reliable to number of records and pages to extract.
"""

# BY DEFAULT pdf downloader is turned off in run_debug func
TEST_CONFIGS = {
    "1": {
        "label": "Texas Instruments - sensors",
        "url": "https://www.ti.com/product-category/sensors/products.html#",
        "user_prompt": TI_PROMPT,
        "user_filter_choice": ["Product number", "part number"],
        "user_csv_list": ["TMP75", "TMP117", "TMP9R01-SP", "TMCS1170", "ISOTMP35R", "FAN31790", "ISO"],
        "output_path": "data/excel/output_test_1.xlsx"
    },
    "2": {
        "label": "Analog Devices - parametric search",
        "url": "https://www.analog.com/en/parametricsearch/2475#/",
        "user_prompt": GENERIC_PROMPT,
        "user_filter_choice": ["Parts", "Filter Parts"],
        "user_csv_list": ["ADX"],
        "output_path": "data/excel/output_test_2.xlsx"
    },
    "3": {
        "label": "Vishay - LEDs SMD",
        "url": "https://www.vishay.com/en/leds/smd/",
        "user_prompt": GENERIC_PROMPT,
        "filters": [
            {
                "name": "Part Number",
                "hints": ["VLMK3100", "VLRYG311N2Q1"],
                "values": ["TLMG1100", "VLMK3100", "VLRYG311N2Q1"]
            }
        ],
        "output_path": "data/excel/output_test_3.xlsx"
    },
    "4": {
        "label": "Infineon - digital isolators",
        "url": "https://www.infineon.com/products/isolation/digital-isolators#products",
        "user_prompt": GENERIC_PROMPT,
        "user_filter_choice": ["Part number", "Select..."],
        "user_csv_list": ["2DIB1401F", "2DIB1400F", "2DIB1411F", "4DIR2400H"],
        "output_path": "data/excel/output_test_4.xlsx"
    },
    "5": {
        "label": "KOA Speer",
        "url": "https://www.koaspeer.com/parametric/",  
        "user_prompt": GENERIC_PROMPT,
        "filters": [
            {
                "name": "Resistance Range - Ohms",
                "hints": ["Choose One", "100 - 10 k"],
                "values": ["100 - 10 k"]
            },
            {
                "name": "Minimum Power Rating - Watts",
                "hints": ["Choose One", "0.03 - 0.1"],
                "values": ["0.03 - 0.1"]
            },
            {
                "name": "Tolerance ±%",
                "hints": ["Choose One", "<= 0.05"],
                "values": ["<= 0.05"]
            }
        ],
        "output_path": "data/excel/output_test_5.xlsx"
    },
    "6": {
        "label": "Multi-filter test (template) Samtec",
        "url": "https://www.samtec.com/products/cle?_gl=1*1dsoqeg*_up*MQ..*_ga*NjM0OTQ5ODc4LjE3ODk2NDA1Mjg.*_ga_3KFNZC07WW*czE3ODk2NDA1MjckbzEkZzEkdDE3ODk2NDA2NDAkajckbDAkaDE3MjE2MTYxNzg.",
        "user_prompt": SAMTEC_PROMPT,
        "filters": [],
        "output_path": "data/excel/output_test_6.xlsx"
    },
    "7": {
        "label": "Amphenol",
        "url": "https://amphenol.com/products/antenna-solutions", # it can scrape the date but there is only a few data provided
        "user_prompt": AMPHENOL_PROMPT,
        "user_filter_choice": ["Product Name", "Product Search"],
        "user_csv_list": ["5G"],
        "output_path": "data/excel/output_test_7.xlsx"
    },
    "8": {
        "label": "TE Connectivity - power connectors",
        "url": "https://www.te.com/en/plp/das-antennas/Y44rw.html", # it works fine, may have some bugs in future more complex tests
        "user_prompt": GENERIC_PROMPT,
        "user_filter_choice": ["Communication Protocol", "ISM"],
        "user_csv_list": ["ISM"],
        "output_path": "data/excel/output_test_8.xlsx"
    },
    "9": {
        "label": "Molex1",
        "url": "https://www.molex.com/en-us/part-list/connectors", # wokrs fine
        "user_prompt": GENERIC_PROMPT,
        "user_filter_choice": ["Product Name", "Micro-Latch"],
        "user_csv_list": ["Micro-Latch"],
        "output_path": "data/excel/output_test_9.xlsx"
    },
    "10": {
        "label": "Molex2 - Multi-filter test (template)",
        "url": "https://www.molex.com/en-us/part-list/connectors",
        "user_prompt": GENERIC_PROMPT,
        "filters": [
        {
            "name": "Category",
            "hints": ["I/O Connectors"],
            "values": ["I/O Connectors"]
        },
        {
            "name": "Product Name",
            "hints": ["2.00mm I/O Connectors"],
            "values": ["2.00mm I/O Connectors"]
        },
        {
            "name": "Type",
            "hints": ["N/A"],
            "values": ["N/A"]
        },
        {
            "name": "Circuits (Loaded)",
            "hints": [],
            "values": ["6"]
        }
    ],
        "output_path": "data/excel/output_test_10.xlsx"
    },
}


def main():
    orchestrator = Orchestrator()

    print("Choose site for the test:")
    for key, cfg in TEST_CONFIGS.items():
        print(f"  {key}. {cfg['label']}")

    choice = input("Number: ").strip()
    config = TEST_CONFIGS.get(choice)

    if not config:
        print(f"Unknown option: '{choice}'")
        return

    if "filters" in config:
        filters = config["filters"]
        # do not aggregate filters into legacy arguments; pass filters directly
        user_filter_choice = []
        user_csv_list = []
    else:
        filters = None
        user_filter_choice = config["user_filter_choice"]
        user_csv_list = config["user_csv_list"]

    orchestrator.run_debug(
        url=config["url"],
        user_prompt=config["user_prompt"],
        user_filter_choice=user_filter_choice,
        user_csv_list=user_csv_list,
        output_path=config["output_path"],
        filters=filters
    )


if __name__ == "__main__":
    main()