import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal

import psycopg
import pytest

from src.ledger.service import post_transaction, reverse_transaction, get_balance
from src.ledger.reconciliation import reconcile

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://digital_bank:digital_bank@localhost:5432/digital_bank_test",
)


class TestNonNegativeBalance:

    def test_overdraft_is_rejected(self, conn, make_account):
        customer = make_account(currency="BRL", normal_balance="credit")
        external = make_account(
            code=f"external:deposits:BRL:{uuid.uuid4()}",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )

        post_transaction(
            conn,
            idempotency_key=str(uuid.uuid4()),
            description="seed 100",
            entries=[
                (external, "debit", "100.0000", "BRL"),
                (customer, "credit", "100.0000", "BRL"),
            ],
        )

        with pytest.raises(Exception, match="Insufficient funds"):
            post_transaction(
                conn,
                idempotency_key=str(uuid.uuid4()),
                description="withdraw 200 - should fail",
                entries=[
                    (customer, "debit", "200.0000", "BRL"),
                    (external, "credit", "200.0000", "BRL"),
                ],
            )

    def test_system_account_can_go_negative(self, conn, make_account):
        customer = make_account(currency="BRL", normal_balance="credit")
        external = make_account(
            code=f"external:deposits:BRL:{uuid.uuid4()}",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )

        post_transaction(
            conn,
            idempotency_key=str(uuid.uuid4()),
            description="deposit makes external go negative",
            entries=[
                (external, "debit", "1000.0000", "BRL"),
                (customer, "credit", "1000.0000", "BRL"),
            ],
        )

        row = conn.execute(
            "SELECT balance FROM account WHERE id = %s", (str(external),)
        ).fetchone()
        assert row[0] == Decimal("-1000.0000")


class TestConcurrency:

    def _try_debit(self, customer_id, external_id, amount):
        """Each thread gets its own connection."""
        try:
            with psycopg.connect(DATABASE_URL) as conn:
                post_transaction(
                    conn,
                    idempotency_key=str(uuid.uuid4()),
                    description=f"debit {amount}",
                    entries=[
                        (customer_id, "debit", amount, "BRL"),
                        (external_id, "credit", amount, "BRL"),
                    ],
                )
            return "success"
        except Exception:
            return "rejected"

    def test_100_concurrent_debits_only_one_succeeds(self, conn, make_account):
        customer = make_account(currency="BRL", normal_balance="credit")
        external = make_account(
            code=f"external:deposits:BRL:{uuid.uuid4()}",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )

        post_transaction(
            conn,
            idempotency_key=str(uuid.uuid4()),
            description="seed 100",
            entries=[
                (external, "debit", "100.0000", "BRL"),
                (customer, "credit", "100.0000", "BRL"),
            ],
        )

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = [
                pool.submit(self._try_debit, customer, external, "80.0000")
                for _ in range(100)
            ]
            results = [f.result() for f in as_completed(futures)]

        successes = results.count("success")
        assert successes == 1, f"Expected 1 success, got {successes}"

        row = conn.execute(
            "SELECT balance FROM account WHERE id = %s", (str(customer),)
        ).fetchone()
        assert row[0] == Decimal("20.0000")

    def _try_transfer(self, from_id, to_id, amount):
        try:
            with psycopg.connect(DATABASE_URL) as conn:
                post_transaction(
                    conn,
                    idempotency_key=str(uuid.uuid4()),
                    description="transfer",
                    entries=[
                        (from_id, "debit", amount, "BRL"),
                        (to_id, "credit", amount, "BRL"),
                    ],
                )
            return "success"
        except Exception:
            return "rejected"

    def test_no_deadlock_on_bidirectional_transfers(self, conn, make_account):
        alice = make_account(currency="BRL", normal_balance="credit")
        bob = make_account(currency="BRL", normal_balance="credit")
        external = make_account(
            code=f"external:deposits:BRL:{uuid.uuid4()}",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )

        for acc in (alice, bob):
            post_transaction(
                conn,
                idempotency_key=str(uuid.uuid4()),
                description="seed",
                entries=[
                    (external, "debit", "10000.0000", "BRL"),
                    (acc, "credit", "10000.0000", "BRL"),
                ],
            )

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = []
            for i in range(50):
                futures.append(pool.submit(self._try_transfer, alice, bob, "1.0000"))
                futures.append(pool.submit(self._try_transfer, bob, alice, "1.0000"))
            results = [f.result() for f in as_completed(futures)]

        assert "rejected" not in results, "No transfer should be rejected"

        balance_a = conn.execute(
            "SELECT balance FROM account WHERE id = %s", (str(alice),)
        ).fetchone()[0]
        balance_b = conn.execute(
            "SELECT balance FROM account WHERE id = %s", (str(bob),)
        ).fetchone()[0]
        assert balance_a + balance_b == Decimal("20000.0000")


class TestIdempotency:

    def test_same_key_is_rejected(self, conn, make_account):
        customer = make_account(currency="BRL", normal_balance="credit")
        external = make_account(
            code=f"external:deposits:BRL:{uuid.uuid4()}",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )
        key = str(uuid.uuid4())

        post_transaction(
            conn,
            idempotency_key=key,
            description="deposit",
            entries=[
                (external, "debit", "100.0000", "BRL"),
                (customer, "credit", "100.0000", "BRL"),
            ],
        )

        with pytest.raises(Exception, match="idempotency_key"):
            post_transaction(
                conn,
                idempotency_key=key,
                description="same deposit again",
                entries=[
                    (external, "debit", "100.0000", "BRL"),
                    (customer, "credit", "100.0000", "BRL"),
                ],
            )

        conn.rollback()

        row = conn.execute(
            "SELECT balance FROM account WHERE id = %s", (str(customer),)
        ).fetchone()
        assert row[0] == Decimal("100.0000")

    def test_concurrent_idempotency_one_transaction(self, conn, make_account):
        customer = make_account(currency="BRL", normal_balance="credit")
        external = make_account(
            code=f"external:deposits:BRL:{uuid.uuid4()}",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )
        key = str(uuid.uuid4())

        def _try_post():
            try:
                with psycopg.connect(DATABASE_URL) as c:
                    post_transaction(
                        c,
                        idempotency_key=key,
                        description="deposit",
                        entries=[
                            (customer, "credit", "100.0000", "BRL"),
                            (external, "debit", "100.0000", "BRL"),
                        ],
                    )
                return "success"
            except Exception:
                return "rejected"

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(_try_post) for _ in range(10)]
            results = [f.result() for f in as_completed(futures)]

        assert results.count("success") == 1
        assert results.count("rejected") == 9

        row = conn.execute(
            "SELECT count(*) FROM ledger_transaction WHERE idempotency_key = %s",
            (key,),
        ).fetchone()
        assert row[0] == 1

        row = conn.execute(
            "SELECT balance FROM account WHERE id = %s", (str(customer),)
        ).fetchone()
        assert row[0] == Decimal("100.0000")


class TestReversal:

    def test_reversal_zeroes_effect(self, conn, make_account):
        customer = make_account(currency="BRL", normal_balance="credit")
        external = make_account(
            code=f"external:deposits:BRL:{uuid.uuid4()}",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )

        tx_id = post_transaction(
            conn,
            idempotency_key=str(uuid.uuid4()),
            description="deposit 500",
            entries=[
                (external, "debit", "500.0000", "BRL"),
                (customer, "credit", "500.0000", "BRL"),
            ],
        )

        assert get_balance(conn, customer) == Decimal("500.0000")

        reverse_transaction(conn, tx_id, str(uuid.uuid4()))

        assert get_balance(conn, customer) == Decimal("0")
        assert get_balance(conn, external) == Decimal("0")

    def test_original_transaction_still_exists(self, conn, make_account):
        customer = make_account(currency="BRL", normal_balance="credit")
        external = make_account(
            code=f"external:deposits:BRL:{uuid.uuid4()}",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )

        tx_id = post_transaction(
            conn,
            idempotency_key=str(uuid.uuid4()),
            description="deposit",
            entries=[
                (external, "debit", "100.0000", "BRL"),
                (customer, "credit", "100.0000", "BRL"),
            ],
        )

        reverse_transaction(conn, tx_id, str(uuid.uuid4()))

        original = conn.execute(
            "SELECT id FROM ledger_transaction WHERE id = %s",
            (str(tx_id),),
        ).fetchone()
        assert original is not None

        entry_count = conn.execute(
            "SELECT count(*) FROM entry WHERE transaction_id = %s",
            (str(tx_id),),
        ).fetchone()[0]
        assert entry_count == 2

    def test_double_reversal_is_rejected(self, conn, make_account):
        customer = make_account(currency="BRL", normal_balance="credit")
        external = make_account(
            code=f"external:deposits:BRL:{uuid.uuid4()}",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )

        tx_id = post_transaction(
            conn,
            idempotency_key=str(uuid.uuid4()),
            description="deposit",
            entries=[
                (external, "debit", "100.0000", "BRL"),
                (customer, "credit", "100.0000", "BRL"),
            ],
        )

        reverse_transaction(conn, tx_id, str(uuid.uuid4()))

        with pytest.raises(Exception, match="reverses_id"):
            reverse_transaction(conn, tx_id, str(uuid.uuid4()))


class TestReconciliation:

    def test_reconciliation_passes_after_operations(self, conn, make_account):
        customer_a = make_account(currency="BRL", normal_balance="credit")
        customer_b = make_account(currency="BRL", normal_balance="credit")
        external = make_account(
            code=f"external:deposits:BRL:{uuid.uuid4()}",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )

        post_transaction(
            conn,
            idempotency_key=str(uuid.uuid4()),
            description="deposit A",
            entries=[
                (external, "debit", "1000.0000", "BRL"),
                (customer_a, "credit", "1000.0000", "BRL"),
            ],
        )

        post_transaction(
            conn,
            idempotency_key=str(uuid.uuid4()),
            description="A -> B",
            entries=[
                (customer_a, "debit", "300.0000", "BRL"),
                (customer_b, "credit", "300.0000", "BRL"),
            ],
        )

        tx_to_reverse = post_transaction(
            conn,
            idempotency_key=str(uuid.uuid4()),
            description="A -> B (mistake)",
            entries=[
                (customer_a, "debit", "50.0000", "BRL"),
                (customer_b, "credit", "50.0000", "BRL"),
            ],
        )

        reverse_transaction(conn, tx_to_reverse, str(uuid.uuid4()))

        result = reconcile(conn)
        assert result["ok"], (
            f"global: {result['global_imbalances']}, "
            f"mismatches: {result['balance_mismatches']}"
        )

    def test_reconciliation_passes_empty_ledger(self, conn):
        result = reconcile(conn)
        assert result["ok"]

    def test_global_zero_sum_query_returns_nothing(self, conn, make_account):
        customer = make_account(currency="BRL", normal_balance="credit")
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
                (customer, "credit", "500.0000", "BRL"),
            ],
        )

        bad_rows = conn.execute(
            """
            SELECT transaction_id, currency
              FROM entry
             GROUP BY transaction_id, currency
            HAVING sum(CASE direction WHEN 'debit' THEN amount ELSE -amount END) <> 0
            """
        ).fetchall()
        assert len(bad_rows) == 0


class TestBalanceProjection:

    def test_deposit_updates_balance(self, conn, make_account):
        customer = make_account(currency="BRL", normal_balance="credit")
        external = make_account(
            code=f"external:deposits:BRL:{uuid.uuid4()}",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )

        post_transaction(
            conn,
            idempotency_key=str(uuid.uuid4()),
            description="deposit 500",
            entries=[
                (external, "debit", "500.0000", "BRL"),
                (customer, "credit", "500.0000", "BRL"),
            ],
        )

        assert get_balance(conn, customer) == 500
        assert get_balance(conn, external) == -500

    def test_two_deposits_accumulate(self, conn, make_account):
        customer = make_account(currency="BRL", normal_balance="credit")
        external = make_account(
            code=f"external:deposits:BRL:{uuid.uuid4()}",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )

        for i in range(2):
            post_transaction(
                conn,
                idempotency_key=str(uuid.uuid4()),
                description=f"deposit {i}",
                entries=[
                    (external, "debit", "100.0000", "BRL"),
                    (customer, "credit", "100.0000", "BRL"),
                ],
            )

        assert get_balance(conn, customer) == 200
        assert get_balance(conn, external) == -200

    def test_transfer_updates_both_balances(self, conn, make_account):
        alice = make_account(currency="BRL", normal_balance="credit")
        bob = make_account(currency="BRL", normal_balance="credit")
        external = make_account(
            code=f"external:deposits:BRL:{uuid.uuid4()}",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )

        post_transaction(
            conn,
            idempotency_key=str(uuid.uuid4()),
            description="seed alice",
            entries=[
                (external, "debit", "1000.0000", "BRL"),
                (alice, "credit", "1000.0000", "BRL"),
            ],
        )

        post_transaction(
            conn,
            idempotency_key=str(uuid.uuid4()),
            description="alice -> bob 300",
            entries=[
                (alice, "debit", "300.0000", "BRL"),
                (bob, "credit", "300.0000", "BRL"),
            ],
        )

        assert get_balance(conn, alice) == 700
        assert get_balance(conn, bob) == 300


class TestAppendOnly:

    def test_update_entry_is_rejected(self, conn, make_account):
        customer = make_account(currency="BRL", normal_balance="credit")
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
                (external, "debit", "100.0000", "BRL"),
                (customer, "credit", "100.0000", "BRL"),
            ],
        )

        with pytest.raises(Exception, match="append-only"):
            conn.execute("UPDATE entry SET amount = 999 WHERE id = 1")
            conn.commit()

    def test_delete_entry_is_rejected(self, conn, make_account):
        customer = make_account(currency="BRL", normal_balance="credit")
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
                (external, "debit", "100.0000", "BRL"),
                (customer, "credit", "100.0000", "BRL"),
            ],
        )

        with pytest.raises(Exception, match="append-only"):
            conn.execute("DELETE FROM entry WHERE id = 1")
            conn.commit()

    def test_update_transaction_is_rejected(self, conn, make_account):
        customer = make_account(currency="BRL", normal_balance="credit")
        external = make_account(
            code=f"external:deposits:BRL:{uuid.uuid4()}",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )
        tx_id = post_transaction(
            conn,
            idempotency_key=str(uuid.uuid4()),
            description="deposit",
            entries=[
                (external, "debit", "100.0000", "BRL"),
                (customer, "credit", "100.0000", "BRL"),
            ],
        )

        with pytest.raises(Exception, match="append-only"):
            conn.execute(
                "UPDATE ledger_transaction SET description = 'hacked' WHERE id = %s",
                (str(tx_id),),
            )
            conn.commit()

    def test_delete_transaction_is_rejected(self, conn, make_account):
        customer = make_account(currency="BRL", normal_balance="credit")
        external = make_account(
            code=f"external:deposits:BRL:{uuid.uuid4()}",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )
        tx_id = post_transaction(
            conn,
            idempotency_key=str(uuid.uuid4()),
            description="deposit",
            entries=[
                (external, "debit", "100.0000", "BRL"),
                (customer, "credit", "100.0000", "BRL"),
            ],
        )

        with pytest.raises(Exception, match="append-only"):
            conn.execute(
                "DELETE FROM ledger_transaction WHERE id = %s",
                (str(tx_id),),
            )
            conn.commit()


class TestZeroSumInvariant:

    def test_unbalanced_transaction_is_rejected(self, conn, make_account):
        customer = make_account(currency="BRL", normal_balance="credit")
        external = make_account(
            code="external:deposits:BRL",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )

        with pytest.raises(Exception, match="at least 2 entries"):
            post_transaction(
                conn,
                idempotency_key=str(uuid.uuid4()),
                description="bad deposit - only one leg",
                entries=[
                    (external, "debit", "100.0000", "BRL"),
                ],
            )

    def test_balanced_transaction_passes(self, conn, make_account):
        customer = make_account(currency="BRL", normal_balance="credit")
        external = make_account(
            code=f"external:deposits:BRL:{uuid.uuid4()}",
            currency="BRL",
            normal_balance="debit",
            allow_negative=True,
        )

        tx_id = post_transaction(
            conn,
            idempotency_key=str(uuid.uuid4()),
            description="valid deposit",
            entries=[
                (external, "debit", "100.0000", "BRL"),
                (customer, "credit", "100.0000", "BRL"),
            ],
        )
        assert tx_id is not None

    def test_unbalanced_two_entries_is_rejected(self, conn, make_account):
        account_a = make_account(
            currency="BRL", normal_balance="debit", allow_negative=True
        )
        account_b = make_account(
            currency="BRL", normal_balance="debit", allow_negative=True
        )

        with pytest.raises(Exception, match="does not balance"):
            post_transaction(
                conn,
                idempotency_key=str(uuid.uuid4()),
                description="two debits, no credit",
                entries=[
                    (account_a, "debit", "100.0000", "BRL"),
                    (account_b, "debit", "50.0000", "BRL"),
                ],
            )

    def test_cross_currency_is_rejected(self, conn, make_account):
        brl_account = make_account(
            currency="BRL", normal_balance="credit", allow_negative=True
        )
        usd_account = make_account(
            currency="USD", normal_balance="credit", allow_negative=True
        )

        with pytest.raises(Exception, match="does not balance"):
            post_transaction(
                conn,
                idempotency_key=str(uuid.uuid4()),
                description="BRL debit vs USD credit - must fail",
                entries=[
                    (brl_account, "debit", "100.0000", "BRL"),
                    (usd_account, "credit", "100.0000", "USD"),
                ],
            )
