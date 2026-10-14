import os
import uuid

import psycopg
import pytest

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://digital_bank:digital_bank@localhost:5432/digital_bank_test",
)

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "schema.sql")


@pytest.fixture(scope="session")
def db_schema():
    with open(SCHEMA_PATH) as f:
        schema_sql = f.read()

    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("DROP TABLE IF EXISTS entry, ledger_transaction, account, customer CASCADE")
        conn.execute(schema_sql)
        conn.commit()

    yield

    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("DROP TABLE IF EXISTS entry, ledger_transaction, account, customer CASCADE")
        conn.commit()


@pytest.fixture()
def conn(db_schema):
    with psycopg.connect(DATABASE_URL) as connection:
        yield connection
        connection.rollback()


@pytest.fixture()
def make_account(conn):
    def _make(code=None, currency="BRL", normal_balance="credit", allow_negative=False):
        if code is None:
            code = f"test:{uuid.uuid4()}"
        row = conn.execute(
            """
            INSERT INTO account (code, currency, normal_balance, allow_negative)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (code, currency, normal_balance, allow_negative),
        ).fetchone()
        conn.commit()
        return row[0]

    return _make
