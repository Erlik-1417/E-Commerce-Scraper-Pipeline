import sqlite3
import pytest
from bs4 import BeautifulSoup
from scraper import (
    clean_price,
    clean_text,
    init_database,
    parse_page_products,
    parse_rating,
    save_products,
    validate_product,
)


def test_clean_price_valid_formats():
    assert clean_price("£42.96") == 42.96
    assert clean_price("$10.50") == 10.50
    assert clean_price("€99.00") == 99.00
    assert clean_price("15") == 15.0


def test_clean_price_invalid_inputs():
    assert clean_price(None) is None
    assert clean_price("") is None
    assert clean_price("Free") is None
    assert clean_price("Unavailable") is None


def test_clean_text_normalization():
    assert clean_text("   In   stock   \n\t") == "In stock"
    assert clean_text("\n\nBook Title\t\n") == "Book Title"
    assert clean_text(None) == ""
    assert clean_text("") == ""


def test_parse_rating_classes():
    html_three = '<p class="star-rating Three"></p>'
    soup_three = BeautifulSoup(html_three, "html.parser").p
    assert parse_rating(soup_three) == 3

    html_five = '<p class="star-rating Five"></p>'
    soup_five = BeautifulSoup(html_five, "html.parser").p
    assert parse_rating(soup_five) == 5

    html_none = '<p class="other-class"></p>'
    soup_none = BeautifulSoup(html_none, "html.parser").p
    assert parse_rating(soup_none) is None
    assert parse_rating(None) is None


def test_validate_product_success():
    valid_product = {
        "title": "Clean Code",
        "price": 32.50,
        "availability": "In stock",
        "rating": 5,
        "stock_count": 10,
    }
    assert validate_product(valid_product) is True


def test_validate_product_invalid_price():
    invalid_zero_price = {
        "title": "Book",
        "price": 0.0,
        "availability": "In stock",
    }
    invalid_neg_price = {
        "title": "Book",
        "price": -10.0,
        "availability": "In stock",
    }
    invalid_none_price = {
        "title": "Book",
        "price": None,
        "availability": "In stock",
    }
    assert validate_product(invalid_zero_price) is False
    assert validate_product(invalid_neg_price) is False
    assert validate_product(invalid_none_price) is False


def test_validate_product_invalid_fields():
    missing_title = {
        "title": "",
        "price": 20.0,
        "availability": "In stock",
    }
    empty_availability = {
        "title": "Book",
        "price": 20.0,
        "availability": "   ",
    }
    out_of_bounds_rating = {
        "title": "Book",
        "price": 20.0,
        "availability": "In stock",
        "rating": 7,
    }
    negative_stock = {
        "title": "Book",
        "price": 20.0,
        "availability": "In stock",
        "stock_count": -5,
    }
    assert validate_product(missing_title) is False
    assert validate_product(empty_availability) is False
    assert validate_product(out_of_bounds_rating) is False
    assert validate_product(negative_stock) is False


def test_parse_page_products_mock(requests_mock=None):
    sample_html = """
    <html>
        <body>
            <article class="product_pod">
                <h3><a title="Test Book Title" href="catalogue/test-book_1/index.html">Test Book...</a></h3>
                <p class="star-rating Four"></p>
                <div class="product_price">
                    <p class="price_color">£25.00</p>
                    <p class="instock availability">In stock</p>
                </div>
            </article>
        </body>
    </html>
    """
    soup = BeautifulSoup(sample_html, "html.parser")
    products = parse_page_products(
        soup=soup,
        current_url="http://books.toscrape.com/category/books/",
        session=None,
        enable_deep_scrape=False,
    )

    assert len(products) == 1
    item = products[0]
    assert item["title"] == "Test Book Title"
    assert item["price"] == 25.00
    assert item["rating"] == 4
    assert item["availability"] == "In stock"
    assert item["product_url"] == "http://books.toscrape.com/category/books/catalogue/test-book_1/index.html"


def test_database_upsert_and_price_history(tmp_path):
    test_db = str(tmp_path / "test_pipeline.db")
    init_database(test_db)

    product_initial = [
        {
            "upc": "TESTUPC001",
            "title": "The Pragmatic Programmer",
            "price": 40.00,
            "stock_count": 10,
            "availability": "In stock",
            "rating": 5,
            "product_url": "http://books.toscrape.com/pragmatic",
        }
    ]

    initial_stats = save_products(product_initial, db_path=test_db)
    assert initial_stats["inserted"] == 1
    assert initial_stats["updated"] == 0
    assert initial_stats["price_changes"] == 0

    same_stats = save_products(product_initial, db_path=test_db)
    assert same_stats["inserted"] == 0
    assert same_stats["updated"] == 1
    assert same_stats["price_changes"] == 0

    product_discounted = [
        {
            "upc": "TESTUPC001",
            "title": "The Pragmatic Programmer",
            "price": 30.00,
            "stock_count": 8,
            "availability": "In stock",
            "rating": 5,
            "product_url": "http://books.toscrape.com/pragmatic",
        }
    ]

    changed_stats = save_products(product_discounted, db_path=test_db)
    assert changed_stats["inserted"] == 0
    assert changed_stats["updated"] == 1
    assert changed_stats["price_changes"] == 1

    with sqlite3.connect(test_db) as conn:
        cursor = conn.cursor()
        product_row = cursor.execute(
            "SELECT price, stock_count FROM products WHERE upc = 'TESTUPC001'"
        ).fetchone()
        assert product_row == (30.00, 8)

        history_rows = cursor.execute(
            """
            SELECT old_price, new_price, price_change, percentage_change
            FROM price_history
            WHERE upc = 'TESTUPC001' AND old_price IS NOT NULL
            """
        ).fetchall()

        assert len(history_rows) == 1
        old_p, new_p, diff, pct = history_rows[0]
        assert old_p == 40.00
        assert new_p == 30.00
        assert diff == -10.00
        assert pct == -25.00
