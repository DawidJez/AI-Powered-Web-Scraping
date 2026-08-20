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
        "user_filter_choice": ["Dominant Wavelength Min.", "360", "562"],
        "user_csv_list": ["360", "562"],
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
        "url": "https://www.koaspeer.com/products/resistors/",
        "user_prompt": GENERIC_PROMPT,
        "user_filter_choice": ["Search", "Search..."],
        "user_csv_list": ["RK73B"],
        "output_path": "data/excel/output_test_5.xlsx"
    },
    "6": {
        "label": "Samtec",
        "url": "https://www.samtec.com",
        "user_prompt": GENERIC_PROMPT,
        "user_filter_choice": ["TODO"],
        "user_csv_list": ["TODO"],
        "output_path": "data/excel/output_test_6.xlsx"
    },
    "7": {
        "label": "Amphenol",
        "url": "https://amphenol.com",
        "user_prompt": GENERIC_PROMPT,
        "user_filter_choice": ["TODO"],
        "user_csv_list": ["TODO"],
        "output_path": "data/excel/output_test_7.xlsx"
    },
    "8": {
        "label": "TE Connectivity - power connectors",
        "url": "https://www.te.com/en/products/connectors/power-connectors/intersection/",
        "user_prompt": GENERIC_PROMPT,
        "user_filter_choice": ["TODO"],
        "user_csv_list": ["TODO"],
        "output_path": "data/excel/output_test_8.xlsx"
    },
    "9": {
        "label": "Molex",
        "url": "https://www.molex.com/en-us/home",
        "user_prompt": GENERIC_PROMPT,
        "user_filter_choice": ["TODO"],
        "user_csv_list": ["TODO"],
        "output_path": "data/excel/output_test_9.xlsx"
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

    orchestrator.run_debug(
        url=config["url"],
        user_prompt=config["user_prompt"],
        user_filter_choice=config["user_filter_choice"],
        user_csv_list=config["user_csv_list"],
        output_path=config["output_path"] 
    )


if __name__ == "__main__":
    main()