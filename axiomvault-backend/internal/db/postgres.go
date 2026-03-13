package db

import (
	"context"
	"log"
	"os"

	"github.com/jackc/pgx/v5/pgxpool"
)

var DB *pgxpool.Pool

func Connect() {
	dsn := os.Getenv("DATABASE_URL")

	pool, err := pgxpool.New(context.Background(), dsn)
	if err != nil {
		log.Fatal("Unable to connect to database:", err)
	}

	if err := pool.Ping(context.Background()); err != nil {
		log.Fatal("Database ping failed:", err)
	}

	DB = pool
	log.Println("✅ Connected to PostgreSQL")
}
