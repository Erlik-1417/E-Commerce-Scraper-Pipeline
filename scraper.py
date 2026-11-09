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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("EcommerceScraper")

DEFAULT_URL = "http://books.toscrape.com/catalogue/category/books/science_22/index.html"
DEFAULT_DB_PATH = "mindsite_mock_data.db"
REQUEST_TIMEOUT = 15
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

RATING_MAP = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}


def get_http_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def init_database(db_path: str = DEFAULT_DB_PATH) -> None:
    logger.info(f"Initializing database at: {db_path}")
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upc TEXT UNIQUE,
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

        cursor.execute("PRAGMA table_info(products);")
        existing_columns = {row[1] for row in cursor.fetchall()}

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

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                upc TEXT,
                old_price REAL,
                new_price REAL NOT NULL,
                price_change REAL NOT NULL,
                percentage_change REAL NOT NULL,
                recorded_at TIMESTAMP NOT NULL,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
            );
            """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_upc ON products(upc);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_history_pid ON price_history(product_id);")

        cursor.execute(
            """
            DELETE FROM products
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM products
                GROUP BY title
            );
            """
        )

        conn.commit()
    logger.info("Database schema verified (products and price_history tables are ready).")


def clean_price(raw_price: Optional[str]) -> Optional[float]:
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
    if not raw_text:
        return ""
    return " ".join(raw_text.split())


def parse_rating(element: Optional[BeautifulSoup]) -> Optional[int]:
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
    enriched: Dict[str, Any] = {
        "upc": None,
        "stock_count": None,
        "detail_rating": None,
    }

    try:
        response = session.get(detail_url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        upc_th = soup.find("th", string=re.compile(r"^UPC$", re.IGNORECASE))
        if upc_th and upc_th.find_next_sibling("td"):
            enriched["upc"] = clean_text(upc_th.find_next_sibling("td").get_text())

        avail_elem = soup.find("p", class_="instock availability")
        if avail_elem:
            avail_text = avail_elem.get_text()
            stock_match = re.search(r"\((\d+)\s+available\)", avail_text)
            if stock_match:
                enriched["stock_count"] = int(stock_match.group(1))

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
    product_cards = soup.find_all("article", class_="product_pod")
    logger.info(f"Discovered {len(product_cards)} products on page: {current_url}")

    products = []
    for card in product_cards:
        title_tag = card.find("h3")
        title = ""
        relative_url = ""
        if title_tag and title_tag.find("a"):
            anchor = title_tag.find("a")
            title = anchor.get("title") or anchor.get_text()
            relative_url = anchor.get("href", "")
        title = clean_text(title)

        price_tag = card.find("p", class_="price_color")
        price = clean_price(price_tag.get_text()) if price_tag else None

        avail_tag = card.find("p", class_="instock availability")
        availability = clean_text(avail_tag.get_text()) if avail_tag else ""

        star_elem = card.find("p", class_="star-rating")
        rating = parse_rating(star_elem)

        product_url = (
            urllib.parse.urljoin(current_url, relative_url) if relative_url else None
        )

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

        page_products = parse_page_products(
            soup=soup,
            current_url=current_url,
            session=session,
            enable_deep_scrape=enable_deep_scrape,
        )
        all_products.extend(page_products)

        if max_pages and page_count >= max_pages:
            logger.info(f"Reached specified page limit ({max_pages}). Stopping crawl.")
            break

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


def save_products(
    products: List[Dict[str, Any]],
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, int]:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    stats = {
        "inserted": 0,
        "updated": 0,
        "price_changes": 0,
    }

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        cursor = conn.cursor()

        for item in products:
            if not validate_product(item):
                continue

            upc = item.get("upc")
            title = item["title"]
            price = float(item["price"])
            stock_count = item.get("stock_count")
            availability = item["availability"]
            rating = item.get("rating")
            product_url = item.get("product_url")

            existing_record = None
            if upc:
                cursor.execute(
                    "SELECT id, price FROM products WHERE upc = ?;",
                    (upc,),
                )
                existing_record = cursor.fetchone()

            if not existing_record and product_url:
                cursor.execute(
                    "SELECT id, price FROM products WHERE product_url = ?;",
                    (product_url,),
                )
                existing_record = cursor.fetchone()

            if not existing_record:
                cursor.execute(
                    "SELECT id, price FROM products WHERE title = ?;",
                    (title,),
                )
                existing_record = cursor.fetchone()

            if existing_record:
                product_id, current_price = existing_record

                if round(current_price, 2) != round(price, 2):
                    price_diff = round(price - current_price, 2)
                    percent_diff = round((price_diff / current_price) * 100, 2)

                    cursor.execute(
                        """
                        INSERT INTO price_history (
                            product_id,
                            upc,
                            old_price,
                            new_price,
                            price_change,
                            percentage_change,
                            recorded_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            product_id,
                            upc,
                            current_price,
                            price,
                            price_diff,
                            percent_diff,
                            timestamp,
                        ),
                    )
                    stats["price_changes"] += 1
                    logger.info(
                        f"[PRICE ALERT] '{title}' changed: £{current_price:.2f} -> £{price:.2f} "
                        f"({percent_diff:+.2f}%)"
                    )

                cursor.execute(
                    """
                    UPDATE products
                    SET price = ?,
                        stock_count = ?,
                        availability = ?,
                        rating = ?,
                        product_url = ?,
                        scraped_at = ?
                    WHERE id = ?;
                    """,
                    (
                        price,
                        stock_count,
                        availability,
                        rating,
                        product_url,
                        timestamp,
                        product_id,
                    ),
                )
                stats["updated"] += 1

            else:
                cursor.execute(
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
                    (
                        upc,
                        title,
                        price,
                        stock_count,
                        availability,
                        rating,
                        product_url,
                        timestamp,
                    ),
                )
                product_id = cursor.lastrowid
                stats["inserted"] += 1

                cursor.execute(
                    """
                    INSERT INTO price_history (
                        product_id,
                        upc,
                        old_price,
                        new_price,
                        price_change,
                        percentage_change,
                        recorded_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        product_id,
                        upc,
                        None,
                        price,
                        0.0,
                        0.0,
                        timestamp,
                    ),
                )

        conn.commit()

    return stats


def run_pipeline(
    url: str = DEFAULT_URL,
    db_path: str = DEFAULT_DB_PATH,
    max_pages: Optional[int] = None,
    deep_scrape: bool = True,
) -> Dict[str, int]:
    print("=" * 68)
    print("  E-COMMERCE DATA EXTRACTION & INGESTION PIPELINE (v2.0)")
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
        stats = save_products(raw_products, db_path)

        total_affected = stats["inserted"] + stats["updated"]
        print("-" * 68)
        print(
            f"[SUMMARY] Total Extracted: {len(raw_products)} | "
            f"New: {stats['inserted']} | "
            f"Updated: {stats['updated']} | "
            f"Price Changes: {stats['price_changes']}"
        )
        print(f"Successfully processed and synced {total_affected} products.")
        print("=" * 68)
        return stats

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
