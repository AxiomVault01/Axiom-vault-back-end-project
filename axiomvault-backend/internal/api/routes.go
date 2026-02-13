package api

import (
	"github.com/gin-gonic/gin"

	"axiomvault/internal/api/handlers"
)

func RegisterRoutes(
	router *gin.Engine,
	payrollHandler *handlers.PayrollHandler,
	bankAccountHandler *handlers.BankAccountHandler,
) {
	api := router.Group("/api")

	api.POST("/ingestion/payroll", payrollHandler.IngestPayroll)
	api.POST("/ingestion/bank-accounts", bankAccountHandler.IngestBankAccount)
}
