func CanAccessFile(userID, fileID, action string) bool {
	// Owner always allowed
	if IsOwner(userID, fileID) {
		return true
	}

	// Check shared permissions
	perm := GetPermission(userID, fileID)

	if perm.Permission == action && time.Now().Before(perm.ExpiresAt) {
		return true
	}

	return false
}


func ShareFile(fileID, userID string, duration time.Duration) {
	db.Create(&FilePermission{
		FileID: fileID,
		UserID: userID,
		Permission: "DOWNLOAD",
		ExpiresAt: time.Now().Add(duration),
	})
}
