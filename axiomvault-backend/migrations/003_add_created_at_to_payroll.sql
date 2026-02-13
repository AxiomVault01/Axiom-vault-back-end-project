ALTER TABLE payroll_records
ADD COLUMN created_at TIMESTAMP NOT NULL DEFAULT NOW();

ALTER TABLE bank_accounts
ADD COLUMN employee_id UUID REFERENCES employees(id);

