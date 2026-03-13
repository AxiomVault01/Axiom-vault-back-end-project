package models

import "time"

// AuditLog provides a traceable record of all actions taken
// within the system for compliance and accountability.
type AuditLog struct {
	ID        string
	UserID    string
	EntityID  string
	Timestamp time.Time
}
