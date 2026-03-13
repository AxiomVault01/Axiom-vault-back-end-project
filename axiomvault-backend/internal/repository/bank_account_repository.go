package repository

import (
	"context"

	"github.com/jackc/pgx/v5/pgxpool"
)

type BankAccountRepository struct {
	DB *pgxpool.Pool
}

func (r *BankAccountRepository) InsertBankAccount(
	ctx context.Context,
	id string,
	employeeID string,
	accountNumber string,
	bankName string,
) error {

	_, err := r.DB.Exec(ctx,
		`INSERT INTO bank_accounts
		(id, employee_id, account_number, bank_name)
		VALUES ($1, $2, $3, $4)`,
		id,
		employeeID,
		accountNumber,
		bankName,
	)

	return err
}
