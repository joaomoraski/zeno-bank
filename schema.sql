CREATE TABLE account (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code            text UNIQUE NOT NULL,
  currency        char(3) NOT NULL,
  normal_balance  text NOT NULL CHECK (normal_balance IN ('debit','credit')),
  allow_negative  boolean NOT NULL DEFAULT false,
  balance         numeric(20,4) NOT NULL DEFAULT 0,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ledger_transaction (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  idempotency_key  text UNIQUE NOT NULL,
  description      text NOT NULL,
  reverses_id      uuid REFERENCES ledger_transaction(id) UNIQUE,
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE entry (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  transaction_id  uuid NOT NULL REFERENCES ledger_transaction(id),
  account_id      uuid NOT NULL REFERENCES account(id),
  direction       text NOT NULL CHECK (direction IN ('debit','credit')),
  amount          numeric(20,4) NOT NULL CHECK (amount > 0),
  currency        char(3) NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- Zero-sum invariant: every transaction must balance to zero per currency.
-- DEFERRABLE INITIALLY DEFERRED so entries can be inserted in any order
-- within the transaction; the check runs at COMMIT.
CREATE OR REPLACE FUNCTION check_transaction_balance()
RETURNS TRIGGER AS $$
DECLARE
  bad RECORD;
BEGIN
  SELECT transaction_id, currency,
         sum(CASE direction WHEN 'debit' THEN amount ELSE -amount END) AS net
    INTO bad
    FROM entry
   WHERE transaction_id = NEW.transaction_id
   GROUP BY transaction_id, currency
  HAVING sum(CASE direction WHEN 'debit' THEN amount ELSE -amount END) <> 0
   LIMIT 1;

  IF FOUND THEN
    RAISE EXCEPTION 'Transaction % does not balance: currency=%, net=%',
      bad.transaction_id, bad.currency, bad.net;
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_check_transaction_balance
  AFTER INSERT ON entry
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  EXECUTE FUNCTION check_transaction_balance();

-- Minimum 2 entries per transaction, checked at commit.
CREATE OR REPLACE FUNCTION check_min_entries()
RETURNS TRIGGER AS $$
DECLARE
  entry_count integer;
BEGIN
  SELECT count(*) INTO entry_count
    FROM entry
   WHERE transaction_id = NEW.transaction_id;

  IF entry_count < 2 THEN
    RAISE EXCEPTION 'Transaction % must have at least 2 entries, has %',
      NEW.transaction_id, entry_count;
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_check_min_entries
  AFTER INSERT ON entry
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  EXECUTE FUNCTION check_min_entries();

-- Append-only: reject UPDATE and DELETE on entry and ledger_transaction.
CREATE OR REPLACE FUNCTION reject_mutation()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION '% on % is not allowed: ledger is append-only',
    TG_OP, TG_TABLE_NAME;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_entry_no_update
  BEFORE UPDATE ON entry FOR EACH ROW
  EXECUTE FUNCTION reject_mutation();

CREATE TRIGGER trg_entry_no_delete
  BEFORE DELETE ON entry FOR EACH ROW
  EXECUTE FUNCTION reject_mutation();

CREATE TRIGGER trg_ledger_transaction_no_update
  BEFORE UPDATE ON ledger_transaction FOR EACH ROW
  EXECUTE FUNCTION reject_mutation();

CREATE TRIGGER trg_ledger_transaction_no_delete
  BEFORE DELETE ON ledger_transaction FOR EACH ROW
  EXECUTE FUNCTION reject_mutation();

-- Balance projection: update account.balance when entries are inserted.
-- Runs BEFORE the deferred constraint triggers, so the balance is current
-- when the zero-sum and min-entries checks fire.
CREATE OR REPLACE FUNCTION update_account_balance()
RETURNS TRIGGER AS $$
BEGIN
  UPDATE account
     SET balance = balance + CASE NEW.direction
           WHEN 'debit'  THEN -NEW.amount
           WHEN 'credit' THEN  NEW.amount
         END
   WHERE id = NEW.account_id;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_update_account_balance
  AFTER INSERT ON entry
  FOR EACH ROW
  EXECUTE FUNCTION update_account_balance();

-- Non-negative balance: reject if a customer account (allow_negative = false)
-- would go below zero after the balance projection update.
CREATE OR REPLACE FUNCTION check_non_negative_balance()
RETURNS TRIGGER AS $$
BEGIN
  IF NOT NEW.allow_negative AND NEW.balance < 0 THEN
    RAISE EXCEPTION 'Insufficient funds: account % balance would be %',
      NEW.id, NEW.balance;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_check_non_negative_balance
  AFTER UPDATE OF balance ON account
  FOR EACH ROW
  EXECUTE FUNCTION check_non_negative_balance();
