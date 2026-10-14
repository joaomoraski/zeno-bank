def reconcile(conn):
    """Verify ledger integrity.

    Checks:
    1. Global sum per currency is zero
    2. Each account's projected balance matches SUM of its entries
    """
    global_check = conn.execute(
        """
        SELECT currency,
               sum(CASE direction WHEN 'debit' THEN amount ELSE -amount END) AS net
          FROM entry
         GROUP BY currency
        HAVING sum(CASE direction WHEN 'debit' THEN amount ELSE -amount END) <> 0
        """
    ).fetchall()

    balance_mismatches = conn.execute(
        """
        SELECT a.id, a.code, a.balance AS projected,
               COALESCE(sum(CASE e.direction WHEN 'credit' THEN e.amount ELSE -e.amount END), 0) AS derived
          FROM account a
          LEFT JOIN entry e ON e.account_id = a.id
         GROUP BY a.id, a.code, a.balance
        HAVING a.balance <> COALESCE(sum(CASE e.direction WHEN 'credit' THEN e.amount ELSE -e.amount END), 0)
        """
    ).fetchall()

    return {
        "ok": len(global_check) == 0 and len(balance_mismatches) == 0,
        "global_imbalances": global_check,
        "balance_mismatches": balance_mismatches,
    }
