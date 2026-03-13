package main

import (
	"log"

	"github.com/gin-gonic/gin"
	"github.com/joho/godotenv"

	"axiomvault/internal/api"
	"axiomvault/internal/api/handlers"
	"axiomvault/internal/db"
	"axiomvault/internal/repository"
)

func main() {
	godotenv.Load()

	db.Connect()

	// Initialize router
	router := gin.Default()

	// Initialize repositories
	payrollRepo := &repository.PayrollRepository{
		DB: db.DB,
	}

	bankRepo := &repository.BankAccountRepository{
		DB: db.DB,
	}

	// Initialize handlers (Inject dependencies)
	payrollHandler := handlers.NewPayrollHandler(payrollRepo)
	bankAccountHandler := handlers.NewBankAccountHandler(bankRepo)

	// Register routes
	api.RegisterRoutes(router, payrollHandler, bankAccountHandler)

	log.Println("🚀 Server running on :8080")
	router.Run(":8080")
}
