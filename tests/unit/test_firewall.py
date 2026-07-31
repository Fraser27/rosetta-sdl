"""Tests for the SQL firewall."""

import pytest

from src.query.firewall import SQLFirewall


@pytest.fixture
def firewall():
    return SQLFirewall(allowed_tables={"ecommerce.orders", "ecommerce.customers", "ecommerce.products"})


@pytest.fixture
def empty_firewall():
    """Empty allowlist — now fail-closed (deny all), not a no-op."""
    return SQLFirewall(allowed_tables=None)


@pytest.fixture
def disabled_firewall():
    """Explicit opt-out — the only way to allow everything."""
    return SQLFirewall(allow_all=True)


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

    def test_allowed_cte_names_not_flagged_as_tables(self, firewall):
        """CTE names (e.g. from compose_metrics) are internal aliases, not external tables."""
        sql = """
            WITH total_revenue AS (SELECT SUM(total_amount) AS v FROM ecommerce.orders GROUP BY order_date),
                 order_count AS (SELECT COUNT(*) AS v FROM ecommerce.orders GROUP BY order_date)
            SELECT total_revenue.v, order_count.v
            FROM total_revenue
            LEFT JOIN order_count ON total_revenue.order_date = order_count.order_date
        """
        result = firewall.validate(sql)
        assert result.allowed

    def test_fail_closed_on_bad_sql(self, firewall):
        result = firewall.validate("THIS IS NOT SQL AT ALL !!!")
        assert not result.allowed
        assert "parse" in result.reason.lower()

    def test_empty_allowlist_denies_all(self, empty_firewall):
        """Security: an empty allowlist is fail-closed (deny), never allow-all."""
        result = empty_firewall.validate("SELECT * FROM anything.goes")
        assert not result.allowed

    def test_disabled_firewall_allows_all(self, disabled_firewall):
        """allow_all=True is the only way to permit arbitrary tables."""
        result = disabled_firewall.validate("SELECT * FROM anything.goes")
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


# ---------------------------------------------------------------------------
# Provider / allow_all / cache behaviour (P0-2)
# ---------------------------------------------------------------------------


class TestSQLFirewallProvider:
    def test_allow_all_permits_arbitrary_table(self):
        fw = SQLFirewall(allow_all=True)
        assert fw.validate("SELECT * FROM totally.unknown_table").allowed

    def test_provider_allows_and_denies(self):
        fw = SQLFirewall(allowlist_provider=lambda: {"db.t1"})
        assert fw.validate("SELECT * FROM db.t1").allowed
        denied = fw.validate("SELECT * FROM db.t2")
        assert not denied.allowed
        assert "t2" in denied.denied_tables[0]

    def test_provider_result_is_cached_within_ttl(self):
        calls = {"n": 0}

        def provider():
            calls["n"] += 1
            return {"db.t1"}

        # Large TTL so both validate() calls fall inside one cache window.
        fw = SQLFirewall(allowlist_provider=provider, cache_ttl=1000.0)
        assert fw.validate("SELECT * FROM db.t1").allowed
        assert fw.validate("SELECT * FROM db.t1").allowed
        # Provider consulted exactly once despite two validate() calls.
        assert calls["n"] == 1

    def test_provider_exception_denies_without_crashing(self):
        def bad_provider():
            raise RuntimeError("catalog unavailable")

        fw = SQLFirewall(allowlist_provider=bad_provider)
        # Must not raise; falls back to empty last-known set → fail-closed (deny).
        result = fw.validate("SELECT * FROM db.t1")
        assert not result.allowed

    def test_provider_exception_reuses_last_known_set(self):
        state = {"fail": False}

        def flaky_provider():
            if state["fail"]:
                raise RuntimeError("catalog unavailable")
            return {"db.t1"}

        # Tiny TTL so the second validate() refreshes the cache (and hits the error).
        fw = SQLFirewall(allowlist_provider=flaky_provider, cache_ttl=0.0)
        assert fw.validate("SELECT * FROM db.t1").allowed
        state["fail"] = True
        # Provider now fails, but the last-known snapshot keeps db.t1 allowed.
        assert fw.validate("SELECT * FROM db.t1").allowed
