"""Tests for the SQL firewall."""

import pytest

from src.query.firewall import SQLFirewall


@pytest.fixture
def firewall():
    return SQLFirewall(allowed_tables={"ecommerce.orders", "ecommerce.customers", "ecommerce.products"})


@pytest.fixture
def open_firewall():
    return SQLFirewall(allowed_tables=None)


class TestSQLFirewall:
    def test_allowed_simple_select(self, firewall):
        result = firewall.validate("SELECT * FROM ecommerce.orders")
        assert result.allowed

    def test_allowed_join(self, firewall):
        sql = """
            SELECT o.order_id, c.name
            FROM ecommerce.orders o
            JOIN ecommerce.customers c ON o.customer_id = c.customer_id
        """
        result = firewall.validate(sql)
        assert result.allowed

    def test_denied_unauthorized_table(self, firewall):
        result = firewall.validate("SELECT * FROM ecommerce.secret_table")
        assert not result.allowed
        assert "secret_table" in result.denied_tables[0]

    def test_denied_subquery(self, firewall):
        sql = """
            SELECT * FROM ecommerce.orders
            WHERE customer_id IN (SELECT id FROM admin.users)
        """
        result = firewall.validate(sql)
        assert not result.allowed

    def test_denied_cte(self, firewall):
        sql = """
            WITH stolen AS (SELECT * FROM admin.secrets)
            SELECT * FROM stolen
        """
        result = firewall.validate(sql)
        assert not result.allowed

    def test_fail_closed_on_bad_sql(self, firewall):
        result = firewall.validate("THIS IS NOT SQL AT ALL !!!")
        assert not result.allowed
        assert "parse" in result.reason.lower()

    def test_open_firewall_allows_all(self, open_firewall):
        result = open_firewall.validate("SELECT * FROM anything.goes")
        assert result.allowed

    def test_unqualified_table_match(self, firewall):
        # "orders" should match "ecommerce.orders"
        result = firewall.validate("SELECT * FROM orders")
        assert result.allowed

    def test_union_denied(self, firewall):
        sql = """
            SELECT * FROM ecommerce.orders
            UNION ALL
            SELECT * FROM admin.secrets
        """
        result = firewall.validate(sql)
        assert not result.allowed
