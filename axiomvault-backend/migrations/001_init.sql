-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =========================
-- EMPLOYEES
-- =========================
CREATE TABLE employees (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id TEXT UNIQUE NOT NULL,
    full_name TEXT NOT NULL,
    date_of_birth DATE NOT NULL,
    department TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);

-- =========================
-- BANK ACCOUNTS
-- =========================
CREATE TABLE bank_accounts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_number TEXT UNIQUE NOT NULL,
    bank_name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);

-- =========================
-- PAYROLL RECORDS
-- =========================
CREATE TABLE payroll_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id UUID NOT NULL REFERENCES employees(id),
    bank_account_id UUID NOT NULL REFERENCES bank_accounts(id),
    amount_paid NUMERIC(12,2) NOT NULL,
    pay_period DATE NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);

-- =========================
-- FRAUD ALERTS
-- =========================
CREATE TABLE fraud_alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    entity_id UUID,
    description TEXT,
    explanation TEXT,
    created_at TIMESTAMP DEFAULT now()
);

-- =========================
-- AUDIT LOGS
-- =========================
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id UUID,
    created_at TIMESTAMP DEFAULT now()
);
