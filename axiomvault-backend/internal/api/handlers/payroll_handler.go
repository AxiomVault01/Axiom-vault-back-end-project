package handlers

import (
	"net/http"
	"log"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"

	"axiomvault/internal/repository"
)

type PayrollHandler struct {
	Repo *repository.PayrollRepository
}

func NewPayrollHandler(repo *repository.PayrollRepository) *PayrollHandler {
	return &PayrollHandler{
		Repo: repo,
	}
}


type PayrollRequest struct {
	EmployeeID    string  `json:"employee_id" binding:"required"`
	BankAccountID string  `json:"bank_account_id" binding:"required"`
	PayPeriod     string  `json:"pay_period" binding:"required"`
	AmountPaid    float64 `json:"amount_paid" binding:"required"`
}

func (h *PayrollHandler) IngestPayroll(c *gin.Context) {

	var req PayrollRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	err := h.Repo.InsertPayroll(
		c.Request.Context(),
		uuid.NewString(),
		req.EmployeeID,
		req.BankAccountID,
		req.PayPeriod,
		req.AmountPaid,
	)

	// if err != nil {
	// 	c.JSON(http.StatusInternalServerError, gin.H{
	// 		"error": "failed to ingest payroll data",
	// 	})
	// 	return
	// }

	if err != nil {
	log.Println("❌ DATABASE ERROR:", err)

	c.JSON(http.StatusInternalServerError, gin.H{
		"error": err.Error(),
	})
	return
}


	c.JSON(http.StatusCreated, gin.H{
		"message": "payroll record ingested successfully",
	})
}
