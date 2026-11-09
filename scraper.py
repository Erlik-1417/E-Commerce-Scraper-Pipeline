"""
E-Commerce Web Scraping and Data Processing Pipeline
----------------------------------------------------
An automated ETL pipeline designed to extract product data from an e-commerce
catalog, perform data cleaning and validation, and store structured records into
a relational SQLite database with strict integrity checks.

Target Site: Books to Scrape (Science Category)
Database: mindsite_mock_data.db
Table: products
"""

import logging
import re
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import requests
from bs4 import BeautifulSoup

# Configure logging for structured feedback
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("EcommerceScraper")

# Default Configurations
DEFAULT_URL = "http://books.toscrape.com/catalogue/category/books/science_22/index.html"
DEFAULT_DB_PATH = "mindsite_mock_data.db"
REQUEST_TIMEOUT = 15  # seconds
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def init_database(db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Initializes the SQLite database and ensures the target table exists
    with appropriate constraints for data integrity.

    Args:
        db_path (str): Filepath to the SQLite database.
    """
    logger.info(f"Initializing database at: {db_path}")
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                price REAL NOT NULL,
                availability TEXT NOT NULL,
                scraped_at TIMESTAMP NOT NULL,
                CONSTRAINT chk_price CHECK (price > 0),
                CONSTRAINT chk_availability CHECK (length(availability) > 0)
            );
            """
        )
        conn.commit()
    logger.info("Database schema verified (table 'products' is ready).")


def fetch_html(url: str) -> str:
    """
    Fetches raw HTML content from the specified URL with error handling.

    Args:
        url (str): Target webpage URL.

    Returns:
        str: Raw HTML response text.

    Raises:
        requests.RequestException: If network or HTTP errors occur.
    """
    logger.info(f"Fetching target URL: {url}")
    headers = {"User-Agent": USER_AGENT}
    response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    # Ensure correct encoding is detected
    if response.encoding is None or response.encoding == "ISO-8859-1":
        response.encoding = response.apparent_encoding
    return response.text


def clean_price(raw_price: str) -> Optional[float]:
    """
    Extracts numerical value from a raw price string, stripping currency symbols.

    Example:
        '£42.96' -> 42.96

    Args:
        raw_price (str): Raw price text from HTML.

    Returns:
        Optional[float]: Cleaned numerical price, or None if parsing fails.
    """
    if not raw_price:
        return None
    # Match integer or decimal numbers
    match = re.search(r"(\d+(?:\.\d+)?)", raw_price)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def clean_text(raw_text: Optional[str]) -> str:
    """
    Strips excess whitespaces, tabs, and newlines from a string.

    Args:
        raw_text (Optional[str]): Text to sanitize.

    Returns:
        str: Cleaned string.
    """
    if not raw_text:
        return ""
    # Normalize internal and surrounding whitespace
    return " ".join(raw_text.split())


def parse_products(html_content: str) -> List[Dict[str, Any]]:
    """
    Parses product cards from the HTML document.

    Args:
        html_content (str): Raw HTML of the catalog page.

    Returns:
        List[Dict[str, Any]]: List of extracted raw product dictionaries.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    product_cards = soup.find_all("article", class_="product_pod")
    logger.info(f"Discovered {len(product_cards)} product cards in HTML.")

    extracted_items = []
    for card in product_cards:
        # Extract Title: The full title is reliably stored in the <a> title attribute
        title_tag = card.find("h3")
        title = ""
        if title_tag and title_tag.find("a"):
            anchor = title_tag.find("a")
            title = anchor.get("title") or anchor.get_text()
        title = clean_text(title)

        # Extract Price
        price_tag = card.find("p", class_="price_color")
        raw_price = price_tag.get_text() if price_tag else ""
        price = clean_price(raw_price)

        # Extract Availability
        avail_tag = card.find("p", class_="instock availability")
        raw_avail = avail_tag.get_text() if avail_tag else ""
        availability = clean_text(raw_avail)

        extracted_items.append(
            {
                "title": title,
                "price": price,
                "availability": availability,
            }
        )

    return extracted_items


def validate_product(product: Dict[str, Any]) -> bool:
    """
    Applies business rules and validation to product data:
    1. Title must not be empty.
    2. Price must be a valid float and strictly greater than 0.
    3. Availability must not be empty.

    Args:
        product (Dict[str, Any]): Product dictionary to evaluate.

    Returns:
        bool: True if valid, False otherwise.
    """
    title = product.get("title")
    price = product.get("price")
    availability = product.get("availability")

    if not title or not isinstance(title, str) or not title.strip():
        logger.warning(f"Validation failed (missing title): {product}")
        return False

    if price is None or not isinstance(price, (int, float)) or price <= 0:
        logger.warning(f"Validation failed (invalid price <= 0 or None): {product}")
        return False

    if not availability or not isinstance(availability, str) or not availability.strip():
        logger.warning(f"Validation failed (empty availability): {product}")
        return False

    return True


def save_products(products: List[Dict[str, Any]], db_path: str = DEFAULT_DB_PATH) -> int:
    """
    Validates and inserts products into SQLite database using parameterized queries.

    Args:
        products (List[Dict[str, Any]]): Extracted product data.
        db_path (str): Target SQLite database filepath.

    Returns:
        int: Number of records successfully saved.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    valid_records = []

    for item in products:
        if validate_product(item):
            valid_records.append(
                (
                    item["title"],
                    item["price"],
                    item["availability"],
                    timestamp,
                )
            )

    if not valid_records:
        logger.warning("No valid records found to insert.")
        return 0

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT INTO products (title, price, availability, scraped_at)
            VALUES (?, ?, ?, ?);
            """,
            valid_records,
        )
        conn.commit()
        inserted_count = cursor.rowcount

    return inserted_count


def run_pipeline(url: str = DEFAULT_URL, db_path: str = DEFAULT_DB_PATH) -> int:
    """
    Executes the end-to-end scraper pipeline:
    Extraction -> Parsing -> Cleaning -> Validation -> SQL Ingestion.

    Args:
        url (str): Source catalog URL.
        db_path (str): Output SQLite database filepath.

    Returns:
        int: Count of successfully saved records.
    """
    print("=" * 65)
    print("  E-COMMERCE DATA EXTRACTION & INGESTION PIPELINE")
    print("=" * 65)
    print(f"Target URL : {url}")
    print(f"Database   : {db_path}")
    print("-" * 65)

    try:
        init_database(db_path)
        html = fetch_html(url)
        raw_products = parse_products(html)
        saved_count = save_products(raw_products, db_path)

        print("-" * 65)
        print(f"[SUMMARY] Found: {len(raw_products)} | Valid & Inserted: {saved_count}")
        print(f"Successfully scraped and saved {saved_count} products.")
        print("=" * 65)
        return saved_count

    except requests.RequestException as req_err:
        logger.error(f"Network error during execution: {req_err}")
        sys.exit(1)
    except sqlite3.Error as sql_err:
        logger.error(f"Database error during execution: {sql_err}")
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Unexpected pipeline failure: {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    run_pipeline()
