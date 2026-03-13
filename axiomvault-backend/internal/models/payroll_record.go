package models

import "time"

type PayrollRecord struct {
	ID            string
	EmployeeID    string
	BankAccountID string
	AmountPaid    float64
	PayPeriod     string
	CreatedAt     time.Time
}
