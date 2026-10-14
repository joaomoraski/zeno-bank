import uuid


def create_customer(conn, name, email):
    """Create a new customer. Returns the customer id."""
    row = conn.execute(
        """
        INSERT INTO customer (name, email)
        VALUES (%s, %s)
        RETURNING id
        """,
        (name, email),
    ).fetchone()
    conn.commit()
    return row[0]


def open_balance(conn, customer_id, currency):
    """Open a balance in a new currency for a customer.

    Creates a ledger account linked to the customer.
    Enforces one balance per currency per customer.
    Returns the ledger account id.
    """
    code = f"customer:{customer_id}:{currency}"

    row = conn.execute(
        """
        INSERT INTO account (code, currency, normal_balance, customer_id)
        VALUES (%s, %s, 'credit', %s)
        RETURNING id
        """,
        (code, currency, str(customer_id)),
    ).fetchone()
    conn.commit()
    return row[0]


def get_balances(conn, customer_id):
    """Get all balances for a customer.

    Returns a list of {account_id, currency, balance}.
    """
    rows = conn.execute(
        """
        SELECT id, currency, balance
          FROM account
         WHERE customer_id = %s
         ORDER BY currency
        """,
        (str(customer_id),),
    ).fetchall()
    return [
        {"account_id": row[0], "currency": row[1], "balance": row[2]}
        for row in rows
    ]
