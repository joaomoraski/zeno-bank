import uuid
from decimal import Decimal

import pytest

from src.accounts.service import create_customer, open_balance, get_balances
from src.ledger.service import post_transaction, get_balance


class TestCreateCustomer:

    def test_create_customer(self, conn):
        customer_id = create_customer(conn, "Alice", "alice@example.com")
        assert customer_id is not None

        row = conn.execute(
            "SELECT name, email, status FROM customer WHERE id = %s",
            (str(customer_id),),
        ).fetchone()
        assert row[0] == "Alice"
        assert row[1] == "alice@example.com"
        assert row[2] == "active"

    def test_duplicate_email_rejected(self, conn):
        create_customer(conn, "Alice", "dup@example.com")
        with pytest.raises(Exception, match="email"):
            create_customer(conn, "Bob", "dup@example.com")


class TestOpenBalance:

    def test_open_brl_balance(self, conn):
        customer_id = create_customer(conn, "Alice", f"{uuid.uuid4()}@example.com")
        account_id = open_balance(conn, customer_id, "BRL")

        row = conn.execute(
            "SELECT code, currency, normal_balance, customer_id, balance FROM account WHERE id = %s",
            (str(account_id),),
        ).fetchone()
        assert row[0] == f"customer:{customer_id}:BRL"
        assert row[1] == "BRL"
        assert row[2] == "credit"
        assert str(row[3]) == str(customer_id)
        assert row[4] == Decimal("0")

    def test_open_usd_balance(self, conn):
        customer_id = create_customer(conn, "Alice", f"{uuid.uuid4()}@example.com")
        open_balance(conn, customer_id, "BRL")
        usd_id = open_balance(conn, customer_id, "USD")

        row = conn.execute(
            "SELECT currency FROM account WHERE id = %s",
            (str(usd_id),),
        ).fetchone()
        assert row[0] == "USD"

    def test_duplicate_currency_rejected(self, conn):
        customer_id = create_customer(conn, "Alice", f"{uuid.uuid4()}@example.com")
        open_balance(conn, customer_id, "BRL")

        with pytest.raises(Exception):
            open_balance(conn, customer_id, "BRL")

    def test_open_balance_creates_ledger_account(self, conn):
        customer_id = create_customer(conn, "Alice", f"{uuid.uuid4()}@example.com")
        account_id = open_balance(conn, customer_id, "BRL")

        balance = get_balance(conn, account_id)
        assert balance == Decimal("0")


class TestGetBalances:

    def test_empty_balances(self, conn):
        customer_id = create_customer(conn, "Alice", f"{uuid.uuid4()}@example.com")
        balances = get_balances(conn, customer_id)
        assert balances == []

    def test_single_balance(self, conn):
        customer_id = create_customer(conn, "Alice", f"{uuid.uuid4()}@example.com")
        account_id = open_balance(conn, customer_id, "BRL")

        balances = get_balances(conn, customer_id)
        assert len(balances) == 1
        assert balances[0]["currency"] == "BRL"
        assert balances[0]["balance"] == Decimal("0")

    def test_multiple_currencies(self, conn):
        customer_id = create_customer(conn, "Alice", f"{uuid.uuid4()}@example.com")
        open_balance(conn, customer_id, "BRL")
        open_balance(conn, customer_id, "USD")
        open_balance(conn, customer_id, "EUR")

        balances = get_balances(conn, customer_id)
        currencies = [b["currency"] for b in balances]
        assert currencies == ["BRL", "EUR", "USD"]

    def test_balance_reflects_deposit(self, conn, make_account):
        customer_id = create_customer(conn, "Alice", f"{uuid.uuid4()}@example.com")
        account_id = open_balance(conn, customer_id, "BRL")
        external = make_account(
            code=f"external:deposits:BRL:{uuid.uuid4()}",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )

        post_transaction(
            conn,
            idempotency_key=str(uuid.uuid4()),
            description="deposit",
            entries=[
                (external, "debit", "500.0000", "BRL"),
                (account_id, "credit", "500.0000", "BRL"),
            ],
        )

        balances = get_balances(conn, customer_id)
        assert balances[0]["balance"] == Decimal("500.0000")
