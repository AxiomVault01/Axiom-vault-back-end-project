package handlers

import (
	"net/http"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"

	"axiomvault/internal/repository"
)

type BankAccountHandler struct {
	Repo *repository.BankAccountRepository
}

func NewBankAccountHandler(repo *repository.BankAccountRepository) *BankAccountHandler {
	return &BankAccountHandler{
		Repo: repo,
	}
}

type BankAccountRequest struct {
	EmployeeID    string `json:"employee_id" binding:"required"`
	AccountNumber string `json:"account_number" binding:"required"`
	BankName      string `json:"bank_name" binding:"required"`
}

func (h *BankAccountHandler) IngestBankAccount(c *gin.Context) {

	var req BankAccountRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	err := h.Repo.InsertBankAccount(
		c.Request.Context(),
		uuid.NewString(),
		req.EmployeeID,
		req.AccountNumber,
		req.BankName,
	)

	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{
			"error": "failed to ingest bank account data",
		})
		return
	}

	c.JSON(http.StatusCreated, gin.H{
		"message": "bank account ingested successfully",
	})
}
