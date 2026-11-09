# 🛒 E-Commerce Web Scraping & Data Processing Pipeline

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Database](https://img.shields.io/badge/Database-SQLite3-003B57.svg?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![Scraping](https://img.shields.io/badge/Library-BeautifulSoup4%20%7C%20Requests-green.svg)](https://pypi.org/project/beautifulsoup4/)
[![Tests](https://img.shields.io/badge/Tests-Pytest-yellowgreen.svg?logo=pytest&logoColor=white)](https://pytest.org/)
[![CI/CD](https://img.shields.io/badge/CI%2FCD-GitHub%20Actions-2088FF.svg?logo=githubactions&logoColor=white)](https://github.com/Erlik-1417/E-Commerce-Scraper-Pipeline/actions)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An end-to-end automated ETL (Extract, Transform, Load) data pipeline developed for e-commerce catalog monitoring and analytics. Built to showcase core software engineering best practices for an e-commerce intelligence platform, emphasizing **automated data extraction**, **data cleaning**, and **ensuring data integrity via SQL**.

---

## 📌 Project Overview

In the e-commerce analytics industry, collecting reliable, timely, and structurally validated product data is critical for competitive pricing analysis, stock tracking, and market intelligence.

This project implements an automated pipeline that targets e-commerce category feeds (using [Books to Scrape](http://books.toscrape.com/catalogue/category/books/science_22/index.html) as a live testing ground), extracts raw product elements, normalizes pricing and availability metrics, validates records against business logic, and persists them into a local relational database (`mindsite_mock_data.db`).

### Key Highlights
- **Price History & Trend Tracking**: Detects price fluctuations across consecutive crawls and records historical changes (`old_price`, `new_price`, `price_change`, `percentage_change`) in a relational audit table.
- **Intelligent Upsert & Deduplication**: Employs UPC and URL indexing to update existing records instead of generating duplicate entries.
- **Automated Pagination Crawler**: Seamlessly detects and traverses multi-page categories until all catalog records are extracted.
- **Deep Data Enrichment**: Crawls individual product detail pages to extract high-value e-commerce metrics (**UPC / SKU**, **exact stock numbers**, **star ratings**, and **direct product URLs**).
- **HTTP Connection Pooling**: Employs `requests.Session` with HTTP Keep-Alive for high-throughput network operations.
- **Multi-Tier Data Integrity & Migration**: Enforces strict validation at runtime and within the database schema using SQL `CHECK` constraints, foreign keys, and safe schema migration.
- **Automated CI/CD & Testing**: Fully automated GitHub Actions workflow running unit tests (`pytest`), scheduled daily crawls, and publishing database artifacts.

---

## 🏗️ Architecture & Pipeline Flow

The pipeline executes a deterministic, multi-stage ETL workflow:

```mermaid
flowchart TD
    A["Target Catalog URL<br/>(books.toscrape.com)"] -->|HTTP Keep-Alive Session| B["Catalog Crawler (requests.Session)"]
    B -->|Check Pagination| B1{"Next Page Link?"}
    B1 -->|Yes| B2["Resolve Next URL & Loop"]
    B2 --> B
    B1 -->|No / Limit Met| C["HTML DOM Parsing (BeautifulSoup4)"]
    C -->|Extract Card Data| D["Deep Detail Page Fetcher"]
    subgraph Enrichment & Transformation
        D -->|Parse Spec Table| D1["UPC / SKU"]
        D -->|Regex Stock Match| D2["Exact Stock Count (int)"]
        D -->|Class Mapping| D3["Star Rating (1-5 int)"]
        D -->|Regex Currency Extraction| D4["Clean Price (float)"]
    end
    D1 & D2 & D3 & D4 --> E{"Data Integrity Validation"}
    E -->|Price <= 0 OR Empty Fields| F["Discard & Log Warning"]
    E -->|Valid Product Record| G["Upsert & Price Intelligence Engine"]
    subgraph Relational Persistence
        G -->|Match by UPC / URL / Title| G1{"Existing Product?"}
        G1 -->|New Product| H1["INSERT into products<br/>Log Baseline in price_history"]
        G1 -->|Existing Product| H2{"Price Changed?"}
        H2 -->|Yes| H3["INSERT delta into price_history<br/>Emit Price Alert Log"]
        H2 -->|No| H4["Skip price_history entry"]
        H3 & H4 --> H5["UPDATE products (price, stock, timestamp)"]
    end
```

---

## 🚀 Key Features

### 1. Price History Tracking & Anomaly Alerts
- **Change Detection**: When an existing product is re-scraped, the pipeline compares its latest recorded price with the newly extracted value.
- **Audit Logging**: Any variance triggers an entry in `price_history` calculating the exact monetary difference (`price_change`) and relative percentage movement (`percentage_change`), accompanied by terminal alerts:
  ```text
  [PRICE ALERT] 'The Most Perfect Thing' changed: £35.00 -> £42.96 (+22.74%)
  ```
- **Upsert Deduplication**: Automatically updates the latest record in `products` rather than inserting redundant rows.

### 2. Robust Web Crawling & Automated Pagination
- **Multi-Page Traversal**: Intelligently identifies `<li class="next"><a href="...">` pagination tags, resolves relative links, and navigates across catalog pages until the category is completely crawled.
- **Configurable Crawl Limits**: Accepts `--max-pages` to restrict depth for testing or quick audits.
- **Optimized HTTP Session**: Reuses underlying TCP connections via `requests.Session` to avoid TLS handshakes for every detail page request.

### 3. Deep Data Enrichment (Detail Scraping)
Going beyond basic catalog cards, the pipeline performs deep inspection on product detail pages to capture high-value e-commerce fields:
- **UPC / SKU**: Extracts unique hardware/product identification codes from the product specifications table.
- **Stock Availability Count**: Uses regex (`\((\d+)\s+available\)`) to parse exact stock quantities into integers (e.g., `16`) rather than just raw status strings.
- **Star Rating (1–5)**: Maps textual CSS classes (`One`, `Two`, `Three`, `Four`, `Five`) into normalized integer ratings.
- **Product Canonical URL**: Stores the direct, absolute link to the product for downstream auditing.

### 4. Data Cleaning & Transformation
- **Currency Sanitization**: Extracts numerical amounts using regular expressions (`re.search(r"(\d+(?:\.\d+)?)", ...)`), eliminating currency noise (`£`, `$`, `€`) and casting directly to `float`.
- **String Sanitation**: Strips leading/trailing whitespaces, newline characters (`\n`), and redundant tab stops across product titles and stock availability flags.

### 5. SQL Validation & Data Integrity
- **Multi-Level Validation**:
  - **Application-Layer**: Validates that product prices are strictly positive (`price > 0`), ratings are bounded `[1, 5]`, stock count is non-negative (`>= 0`), and mandatory fields (`title`, `availability`) are non-empty prior to database execution.
  - **Database-Layer**: Enforces SQL schema constraints (`CHECK (price > 0)`, `CHECK (length(availability) > 0)`), unique indices on `upc`, and foreign key cascades.
- **Automated Schema Migration**: On initialization, inspects existing tables via SQLite `PRAGMA table_info` and safely performs non-destructive schema migrations (`ALTER TABLE`) if new tables or columns are introduced.
- **SQL Injection Prevention**: Employs parameterized queries (`?`, `?`, `?`, ...) via Python's standard `sqlite3` module.

### 6. Automated CI/CD & Testing
- **Continuous Integration**: GitHub Actions workflow (`.github/workflows/scraper_ci.yml`) automatically triggers on push and pull request to run the full Pytest suite.
- **Scheduled Cron Crawl**: Automatically executes daily at `06:00 UTC` to monitor product catalog changes in the cloud.
- **Artifact Publishing**: Packages and uploads the resulting `mindsite_mock_data.db` as a downloadable artifact after each automated run.

---

## 🗄️ Database Schema

The pipeline provisions a local SQLite database named `mindsite_mock_data.db` with a relational two-table architecture:

### 1. `products` Table (Current Product State)

| Column Name | Data Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` | Unique identifier for each record. |
| `upc` | `TEXT` | `UNIQUE` | Unique product identifier / barcode. |
| `title` | `TEXT` | `NOT NULL` | Cleaned full title of the product. |
| `price` | `REAL` | `NOT NULL, CHECK (price > 0)` | Cleaned price stored as a numerical float. |
| `stock_count` | `INTEGER` | `NULLABLE, CHECK (stock_count >= 0)` | Exact units currently in stock. |
| `availability`| `TEXT` | `NOT NULL, CHECK (length > 0)`| Stock availability state (e.g., `'In stock'`). |
| `rating` | `INTEGER` | `NULLABLE, CHECK (rating BETWEEN 1 AND 5)` | Normalized review score (1 to 5). |
| `product_url` | `TEXT` | `NULLABLE` | Canonical URL pointing to the product page. |
| `scraped_at` | `TIMESTAMP`| `NOT NULL` | UTC timestamp recording when extraction occurred. |

### 2. `price_history` Table (Historical Pricing Audit Log)

| Column Name | Data Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` | Unique entry ID. |
| `product_id` | `INTEGER` | `FOREIGN KEY -> products(id)` | Associated product record ID. |
| `upc` | `TEXT` | `NULLABLE` | Associated product UPC. |
| `old_price` | `REAL` | `NULLABLE` | Previous price prior to change (`NULL` for baseline). |
| `new_price` | `REAL` | `NOT NULL` | Updated price captured during crawl. |
| `price_change` | `REAL` | `NOT NULL` | Absolute difference (`new_price - old_price`). |
| `percentage_change` | `REAL` | `NOT NULL` | Relative price change percentage. |
| `recorded_at` | `TIMESTAMP` | `NOT NULL` | UTC timestamp of detected change. |

---

## 🛠️ Tech Stack

- **Language**: Python 3.10+
- **HTTP Client**: [`requests`](https://requests.readthedocs.io/) (Session Connection Pooling)
- **HTML Parser**: [`beautifulsoup4`](https://www.crummy.com/software/BeautifulSoup/)
- **Database Engine**: [`sqlite3`](https://docs.python.org/3/library/sqlite3.html) (Relational with Foreign Keys)
- **Testing Framework**: [`pytest`](https://pytest.org/)
- **Automation / DevOps**: GitHub Actions CI/CD (Cron, Tests, Artifacts)
- **URL Handling**: `urllib.parse`
- **CLI Parsing**: `argparse`

---

## 📂 Project Structure

```text
E-Commerce-Scraper-Pipeline/
│
├── .github/
│   └── workflows/
│       └── scraper_ci.yml     # Automated CI/CD pipeline (Cron & Pytest)
├── scraper.py                 # Core scraping, transformation, upsert & price tracking pipeline
├── test_scraper.py            # Complete Pytest test suite with isolated fixtures
├── requirements.txt           # Project dependencies (requests, beautifulsoup4, pytest)
├── .gitignore                 # Standard Python/IDE/DB ignore rules
└── README.md                  # Comprehensive portfolio documentation
```

---

## ⚙️ How to Run

### 1. Prerequisites
- Python 3.8 or higher installed on your system.
- Git installed.

### 2. Clone the Repository
```bash
git clone https://github.com/Erlik-1417/E-Commerce-Scraper-Pipeline.git
cd E-Commerce-Scraper-Pipeline
```

### 3. Create and Activate a Virtual Environment
```bash
# On macOS / Linux:
python3 -m venv .venv
source .venv/bin/activate

# On Windows (PowerShell):
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 4. Install Dependencies
```bash
pip install -r requirements.txt
```

### 5. Run the Test Suite
Execute all unit and integration tests:
```bash
pytest -v
```

### 6. Execute the Pipeline
Run the default scraper (Science category, full deep enrichment, upsert enabled):
```bash
python scraper.py
```

#### CLI Options
```bash
# Scrape with page limit:
python scraper.py --max-pages 2

# Scrape another category:
python scraper.py --url "http://books.toscrape.com/catalogue/category/books/travel_2/index.html"

# Run fast crawl without detail page scraping:
python scraper.py --no-deep-scrape
```

### 7. Terminal Output Example
```text
====================================================================
  E-COMMERCE DATA EXTRACTION & INGESTION PIPELINE (v2.0)
====================================================================
Target URL   : http://books.toscrape.com/catalogue/category/books/science_22/index.html
Database     : mindsite_mock_data.db
Deep Scrape  : Enabled (UPC, Stock, Rating, URL)
Max Pages    : All Available
--------------------------------------------------------------------
[2026-11-09 23:26:21] [INFO] Initializing database at: mindsite_mock_data.db
[2026-11-09 23:26:21] [INFO] Database schema verified (products and price_history tables are ready).
[2026-11-09 23:26:21] [INFO] Crawling Page #1: http://books.toscrape.com/catalogue/category/books/science_22/index.html
[2026-11-09 23:26:22] [INFO] Discovered 14 products on page: http://books.toscrape.com/catalogue/category/books/science_22/index.html
[2026-11-09 23:26:24] [INFO] No further pagination detected. Category crawl complete.
[2026-11-09 23:26:24] [INFO] Crawl completed across 1 page(s). Total products extracted: 14
--------------------------------------------------------------------
[SUMMARY] Total Extracted: 14 | New: 0 | Updated: 14 | Price Changes: 0
Successfully processed and synced 14 products.
====================================================================
```

### 8. Inspecting Price History & Products
Query price changes and current stock directly:
```bash
python -c "import sqlite3; conn = sqlite3.connect('mindsite_mock_data.db'); print('Price history entries:', conn.cursor().execute('SELECT product_id, upc, old_price, new_price, percentage_change, recorded_at FROM price_history LIMIT 3').fetchall())"
```

---

## 🔮 Roadmap & Milestones

- [x] Automated Pagination Handling
- [x] Deep Product Detail Enrichment (UPC, Stock Count, Star Rating, Canonical URL)
- [x] Price History Tracking & Anomaly Alerts (Slowly Changing Dimensions)
- [x] Zero-clutter, clean codebase
- [x] Automated Test Suite (`pytest` with mock fixtures)
- [x] Continuous Integration & Scheduled Crawl via GitHub Actions

---

## 👤 Author

Developed by **Erlik-1417**  
GitHub: [@Erlik-1417](https://github.com/Erlik-1417)
