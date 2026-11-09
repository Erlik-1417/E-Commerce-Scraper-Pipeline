"""
E-Commerce Web Scraping and Data Processing Pipeline (v1.1)
------------------------------------------------------------
An automated ETL pipeline designed to extract product data from an e-commerce
catalog, perform data cleaning and validation, and store structured records into
a relational SQLite database with strict integrity checks.

Features:
- Automated Pagination Crawler: Navigates category pages seamlessly.
- Deep Data Enrichment: Extracts UPC/SKU, Stock Count, Star Rating, and Product URLs.
- Connection Pooling: Reuses HTTP sessions for high-performance extraction.
- Data Integrity: Multi-tier validation (Python rules + SQLite CHECK constraints).
- Safe Schema Migration: Automatically upgrades existing databases without data loss.

Target Site: Books to Scrape (Science Category)
Database: mindsite_mock_data.db
Table: products
"""

import argparse
import logging
import re
import sqlite3
import sys
import urllib.parse
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

# Textual rating to numerical mapping
RATING_MAP = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}


def get_http_session() -> requests.Session:
    """
    Creates and configures a requests.Session with HTTP Keep-Alive
    and custom headers for optimal connection pooling and throughput.

    Returns:
        requests.Session: Configured session instance.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def init_database(db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Initializes the SQLite database and ensures the target table exists.
    Performs automated schema migrations if previous versions exist.

    Args:
        db_path (str): Filepath to the SQLite database.
    """
    logger.info(f"Initializing database at: {db_path}")
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        # Create table with complete schema if it doesn't exist
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upc TEXT,
                title TEXT NOT NULL,
                price REAL NOT NULL,
                stock_count INTEGER,
                availability TEXT NOT NULL,
                rating INTEGER,
                product_url TEXT,
                scraped_at TIMESTAMP NOT NULL,
                CONSTRAINT chk_price CHECK (price > 0),
                CONSTRAINT chk_availability CHECK (length(availability) > 0)
            );
            """
        )

        # Check existing table columns to perform safe migration if needed
        cursor.execute("PRAGMA table_info(products);")
        existing_columns = {row[1] for row in cursor.fetchall()}

        # Migration columns for enriched data
        enrichment_columns = [
            ("upc", "TEXT"),
            ("stock_count", "INTEGER"),
            ("rating", "INTEGER"),
            ("product_url", "TEXT"),
        ]

        for col_name, col_type in enrichment_columns:
            if col_name not in existing_columns:
                logger.info(f"Migrating schema: adding column '{col_name}' ({col_type})")
                cursor.execute(f"ALTER TABLE products ADD COLUMN {col_name} {col_type};")

        conn.commit()
    logger.info("Database schema verified (table 'products' is ready and up to date).")


def clean_price(raw_price: Optional[str]) -> Optional[float]:
    """
    Extracts numerical value from a raw price string, stripping currency symbols.

    Example:
        '£42.96' -> 42.96

    Args:
        raw_price (Optional[str]): Raw price text from HTML.

    Returns:
        Optional[float]: Cleaned numerical price, or None if parsing fails.
    """
    if not raw_price:
        return None
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
    return " ".join(raw_text.split())


def parse_rating(element: Optional[BeautifulSoup]) -> Optional[int]:
    """
    Converts CSS star-rating classes into an integer score (1-5).

    Example:
        <p class="star-rating Four"> -> 4

    Args:
        element (Optional[BeautifulSoup]): Tag containing star-rating class.

    Returns:
        Optional[int]: Score from 1 to 5, or None if not found.
    """
    if not element:
        return None
    classes = element.get("class", [])
    for cls in classes:
        cls_lower = cls.lower()
        if cls_lower in RATING_MAP:
            return RATING_MAP[cls_lower]
    return None


def fetch_product_details(
    session: requests.Session,
    detail_url: str,
) -> Dict[str, Any]:
    """
    Performs deep scraping on a product's individual detail page to extract
    extended e-commerce metrics: UPC/SKU, exact stock count, and description.

    Args:
        session (requests.Session): Active HTTP session.
        detail_url (str): Absolute URL to the product detail page.

    Returns:
        Dict[str, Any]: Dictionary containing enriched fields.
    """
    enriched: Dict[str, Any] = {
        "upc": None,
        "stock_count": None,
        "detail_rating": None,
    }

    try:
        response = session.get(detail_url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # 1. Extract UPC from specification table
        upc_th = soup.find("th", string=re.compile(r"^UPC$", re.IGNORECASE))
        if upc_th and upc_th.find_next_sibling("td"):
            enriched["upc"] = clean_text(upc_th.find_next_sibling("td").get_text())

        # 2. Extract Exact Available Stock Number from "In stock (X available)"
        avail_elem = soup.find("p", class_="instock availability")
        if avail_elem:
            avail_text = avail_elem.get_text()
            stock_match = re.search(r"\((\d+)\s+available\)", avail_text)
            if stock_match:
                enriched["stock_count"] = int(stock_match.group(1))

        # 3. Extract Rating (fallback or validation)
        star_elem = soup.find("p", class_="star-rating")
        if star_elem:
            enriched["detail_rating"] = parse_rating(star_elem)

    except requests.RequestException as exc:
        logger.warning(f"Failed to fetch detail page ({detail_url}): {exc}")

    return enriched


def parse_page_products(
    soup: BeautifulSoup,
    current_url: str,
    session: requests.Session,
    enable_deep_scrape: bool = True,
) -> List[Dict[str, Any]]:
    """
    Parses product cards on a catalog page and optionally triggers
    deep scraping for each product.

    Args:
        soup (BeautifulSoup): Parsed HTML of current page.
        current_url (str): URL of current page (for relative URL resolution).
        session (requests.Session): HTTP session.
        enable_deep_scrape (bool): Whether to fetch individual detail pages.

    Returns:
        List[Dict[str, Any]]: List of extracted and enriched product records.
    """
    product_cards = soup.find_all("article", class_="product_pod")
    logger.info(f"Discovered {len(product_cards)} products on page: {current_url}")

    products = []
    for card in product_cards:
        # Title extraction (prefers full title attribute)
        title_tag = card.find("h3")
        title = ""
        relative_url = ""
        if title_tag and title_tag.find("a"):
            anchor = title_tag.find("a")
            title = anchor.get("title") or anchor.get_text()
            relative_url = anchor.get("href", "")
        title = clean_text(title)

        # Price extraction
        price_tag = card.find("p", class_="price_color")
        price = clean_price(price_tag.get_text()) if price_tag else None

        # Availability text
        avail_tag = card.find("p", class_="instock availability")
        availability = clean_text(avail_tag.get_text()) if avail_tag else ""

        # Star Rating from card
        star_elem = card.find("p", class_="star-rating")
        rating = parse_rating(star_elem)

        # Resolve Absolute Product URL
        product_url = (
            urllib.parse.urljoin(current_url, relative_url) if relative_url else None
        )

        # Deep scraping: Enrich with UPC and exact Stock Count
        upc = None
        stock_count = None
        if enable_deep_scrape and product_url:
            details = fetch_product_details(session, product_url)
            upc = details.get("upc")
            stock_count = details.get("stock_count")
            if rating is None and details.get("detail_rating"):
                rating = details.get("detail_rating")

        products.append(
            {
                "upc": upc,
                "title": title,
                "price": price,
                "stock_count": stock_count,
                "availability": availability,
                "rating": rating,
                "product_url": product_url,
            }
        )

    return products


def crawl_catalog(
    start_url: str = DEFAULT_URL,
    max_pages: Optional[int] = None,
    enable_deep_scrape: bool = True,
) -> List[Dict[str, Any]]:
    """
    Crawls category pages with automatic pagination following 'next' links.

    Args:
        start_url (str): The initial category URL.
        max_pages (Optional[int]): Maximum number of pages to traverse (None = all).
        enable_deep_scrape (bool): Whether to perform deep scraping on detail pages.

    Returns:
        List[Dict[str, Any]]: Aggregated list of all extracted products.
    """
    session = get_http_session()
    all_products: List[Dict[str, Any]] = []
    current_url: Optional[str] = start_url
    page_count = 0

    while current_url:
        page_count += 1
        logger.info(f"Crawling Page #{page_count}: {current_url}")

        try:
            response = session.get(current_url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            if response.encoding is None or response.encoding == "ISO-8859-1":
                response.encoding = response.apparent_encoding
            soup = BeautifulSoup(response.text, "html.parser")
        except requests.RequestException as err:
            logger.error(f"Failed to fetch page {current_url}: {err}")
            break

        # Parse current page products
        page_products = parse_page_products(
            soup=soup,
            current_url=current_url,
            session=session,
            enable_deep_scrape=enable_deep_scrape,
        )
        all_products.extend(page_products)

        # Check maximum pages limit
        if max_pages and page_count >= max_pages:
            logger.info(f"Reached specified page limit ({max_pages}). Stopping crawl.")
            break

        # Check for Pagination: Look for <li class="next"><a href="...">
        next_tag = soup.find("li", class_="next")
        if next_tag and next_tag.find("a"):
            next_href = next_tag.find("a").get("href")
            current_url = urllib.parse.urljoin(current_url, next_href)
            logger.info(f"Found next page link -> {current_url}")
        else:
            logger.info("No further pagination detected. Category crawl complete.")
            current_url = None

    logger.info(
        f"Crawl completed across {page_count} page(s). Total products extracted: {len(all_products)}"
    )
    return all_products


def validate_product(product: Dict[str, Any]) -> bool:
    """
    Applies business rules and validation to product data:
    1. Title must not be empty.
    2. Price must be a valid float and strictly greater than 0.
    3. Availability must not be empty.
    4. Rating (if present) must be between 1 and 5.
    5. Stock count (if present) must be >= 0.

    Args:
        product (Dict[str, Any]): Product dictionary to evaluate.

    Returns:
        bool: True if valid, False otherwise.
    """
    title = product.get("title")
    price = product.get("price")
    availability = product.get("availability")
    rating = product.get("rating")
    stock_count = product.get("stock_count")

    if not title or not isinstance(title, str) or not title.strip():
        logger.warning(f"Validation failed (missing title): {product}")
        return False

    if price is None or not isinstance(price, (int, float)) or price <= 0:
        logger.warning(f"Validation failed (invalid price <= 0 or None): {product}")
        return False

    if not availability or not isinstance(availability, str) or not availability.strip():
        logger.warning(f"Validation failed (empty availability): {product}")
        return False

    if rating is not None and (not isinstance(rating, int) or not (1 <= rating <= 5)):
        logger.warning(f"Validation failed (rating outside [1, 5]): {product}")
        return False

    if stock_count is not None and (not isinstance(stock_count, int) or stock_count < 0):
        logger.warning(f"Validation failed (negative stock count): {product}")
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
                    item.get("upc"),
                    item["title"],
                    item["price"],
                    item.get("stock_count"),
                    item["availability"],
                    item.get("rating"),
                    item.get("product_url"),
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
            INSERT INTO products (
                upc,
                title,
                price,
                stock_count,
                availability,
                rating,
                product_url,
                scraped_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            valid_records,
        )
        conn.commit()
        inserted_count = cursor.rowcount

    return inserted_count


def run_pipeline(
    url: str = DEFAULT_URL,
    db_path: str = DEFAULT_DB_PATH,
    max_pages: Optional[int] = None,
    deep_scrape: bool = True,
) -> int:
    """
    Executes the end-to-end scraper pipeline:
    Crawling -> Deep Enrichment -> Cleaning -> Validation -> SQL Ingestion.

    Args:
        url (str): Source catalog URL.
        db_path (str): Output SQLite database filepath.
        max_pages (Optional[int]): Maximum pages to scrape.
        deep_scrape (bool): Whether to fetch product detail pages.

    Returns:
        int: Count of successfully saved records.
    """
    print("=" * 68)
    print("  E-COMMERCE DATA EXTRACTION & INGESTION PIPELINE (v1.1)")
    print("=" * 68)
    print(f"Target URL   : {url}")
    print(f"Database     : {db_path}")
    print(f"Deep Scrape  : {'Enabled (UPC, Stock, Rating, URL)' if deep_scrape else 'Disabled'}")
    print(f"Max Pages    : {max_pages if max_pages else 'All Available'}")
    print("-" * 68)

    try:
        init_database(db_path)
        raw_products = crawl_catalog(
            start_url=url,
            max_pages=max_pages,
            enable_deep_scrape=deep_scrape,
        )
        saved_count = save_products(raw_products, db_path)

        print("-" * 68)
        print(f"[SUMMARY] Total Extracted: {len(raw_products)} | Valid & Inserted: {saved_count}")
        print(f"Successfully scraped and saved {saved_count} products.")
        print("=" * 68)
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


def parse_args() -> argparse.Namespace:
    """Configures command-line arguments for pipeline execution."""
    parser = argparse.ArgumentParser(
        description="E-Commerce Catalog Crawler and SQLite Ingestion Pipeline",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=DEFAULT_URL,
        help="Target category URL to scrape (default: Science category).",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=DEFAULT_DB_PATH,
        help="SQLite database filepath (default: mindsite_mock_data.db).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Limit the number of paginated pages to crawl (default: all pages).",
    )
    parser.add_argument(
        "--no-deep-scrape",
        action="store_true",
        help="Disable deep detail page scraping (faster, extracts category-level data only).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_pipeline(
        url=args.url,
        db_path=args.db,
        max_pages=args.max_pages,
        deep_scrape=not args.no_deep_scrape,
    )
