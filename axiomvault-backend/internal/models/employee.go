package models

// EmployeeIdentity represents a unique individual receiving payroll payments.
type EmployeeIdentity struct {
	ID           string // internal system ID
	EmployeeID   string // official employee identifier
	FullName     string
	DateOfBirth  string
	Department   string
	Status       string // active / inactive
}
