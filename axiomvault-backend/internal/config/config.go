package config

import (
	"os"
)

type Config struct {
	DBUrl string
	Port  string
}

func Load() Config {
	return Config{
		DBUrl: os.Getenv("DB_URL"),
		Port:  os.Getenv("SERVER_PORT"),
	}
}
