func DownloadFile(c *gin.Context) {
	userID := c.GetString("user_id")
	fileID := c.Param("id")

	if !CanAccessFile(userID, fileID, "DOWNLOAD") {
		c.JSON(403, gin.H{"error": "Unauthorized"})
		return
	}

	// Decrypt & stream file
	filePath := GetEncryptedPath(fileID)
	DecryptAndSend(filePath, c)
}
