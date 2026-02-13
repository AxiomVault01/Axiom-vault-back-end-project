package repository

import (
	"context"

	"github.com/jackc/pgx/v5/pgxpool"
)

type PayrollRepository struct {
	DB *pgxpool.Pool
}

func (r *PayrollRepository) InsertPayroll(
	ctx context.Context,
	id string,
	employeeID string,
	bankAccountID string,
	payPeriod string,
	amount float64,
) error {

	_, err := r.DB.Exec(ctx,
		`INSERT INTO payroll_records
		(id, employee_id, bank_account_id, amount_paid, pay_period)
		VALUES ($1, $2, $3, $4, $5)`,
		id,
		employeeID,
		bankAccountID,
		amount,
		payPeriod,
	)

	return err
}
