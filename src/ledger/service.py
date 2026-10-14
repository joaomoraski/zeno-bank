import uuid
from decimal import Decimal


def post_transaction(conn, idempotency_key, description, entries, reverses_id=None):
    """Post a ledger transaction with its entries.

    Locks account rows in ID order before inserting to prevent deadlocks
    and enforce serialization on the same account.

    entries: list of (account_id, direction, amount, currency)
    Returns the transaction id.
    """
    tx_id = uuid.uuid4()

    account_ids = sorted(set(str(e[0]) for e in entries))
    conn.execute(
        "SELECT id FROM account WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
        (account_ids,),
    )

    params = [str(tx_id), idempotency_key, description]
    if reverses_id:
        conn.execute(
            """
            INSERT INTO ledger_transaction (id, idempotency_key, description, reverses_id)
            VALUES (%s, %s, %s, %s)
            """,
            (*params, str(reverses_id)),
        )
    else:
        conn.execute(
            """
            INSERT INTO ledger_transaction (id, idempotency_key, description)
            VALUES (%s, %s, %s)
            """,
            params,
        )

    for account_id, direction, amount, currency in entries:
        conn.execute(
            """
            INSERT INTO entry (transaction_id, account_id, direction, amount, currency)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (str(tx_id), str(account_id), direction, amount, currency),
        )
    conn.commit()
    return tx_id


def reverse_transaction(conn, original_tx_id, idempotency_key):
    """Create a reversal: new transaction with inverted entries."""
    original_entries = conn.execute(
        "SELECT account_id, direction, amount, currency FROM entry WHERE transaction_id = %s",
        (str(original_tx_id),),
    ).fetchall()

    flipped_entries = [
        (
            account_id,
            "credit" if direction == "debit" else "debit",
            amount,
            currency,
        )
        for account_id, direction, amount, currency in original_entries
    ]

    return post_transaction(
        conn,
        idempotency_key=idempotency_key,
        description="reversal",
        entries=flipped_entries,
        reverses_id=original_tx_id,
    )


def get_balance(conn, account_id):
    """Read the projected balance of an account."""
    row = conn.execute(
        "SELECT balance FROM account WHERE id = %s", (str(account_id),)
    ).fetchone()
    if row is None:
        raise ValueError(f"Account {account_id} not found")
    return row[0]
