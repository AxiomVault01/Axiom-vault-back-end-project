-- Enable UUID extension if missing
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- EMPLOYEES
ALTER TABLE employees
    ALTER COLUMN employee_id SET NOT NULL,
    ALTER COLUMN full_name SET NOT NULL,
    ALTER COLUMN department SET NOT NULL,
    ALTER COLUMN status SET NOT NULL;

ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now();

-- BANK ACCOUNTS
ALTER TABLE bank_accounts
    ALTER COLUMN account_number SET NOT NULL,
    ALTER COLUMN bank_name SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_bank_accounts_account_number
ON bank_accounts(account_number);

-- PAYROLL RECORDS
ALTER TABLE payroll_records
    ALTER COLUMN employee_id SET NOT NULL,
    ALTER COLUMN bank_account_id SET NOT NULL,
    ALTER COLUMN amount_paid TYPE NUMERIC(12,2),
    ALTER COLUMN pay_period TYPE DATE;

-- FRAUD ALERTS
ALTER TABLE fraud_alerts
    ALTER COLUMN alert_type SET NOT NULL,
    ALTER COLUMN severity SET NOT NULL,
    ALTER COLUMN status SET NOT NULL;

-- AUDIT LOGS
ALTER TABLE audit_logs
    ADD COLUMN IF NOT EXISTS action TEXT,
    ADD COLUMN IF NOT EXISTS entity TEXT;
