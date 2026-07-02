package db

import "database/sql"

// CreateSchema はテスト用の空 DB にテーブルを作成する。
func CreateSchema(d *sql.DB) error {
	stmts := []string{
		`CREATE TABLE IF NOT EXISTS books (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			title TEXT NOT NULL DEFAULT '',
			authors TEXT,
			publisher TEXT,
			published_date TEXT,
			class_number TEXT,
			registration_number TEXT,
			isbn TEXT,
			title_norm TEXT,
			authors_norm TEXT,
			isbn_norm TEXT
		)`,
		`CREATE TABLE IF NOT EXISTS book_covers (
			book_id INTEGER,
			thumbnail TEXT,
			info_link TEXT
		)`,
		`CREATE TABLE IF NOT EXISTS book_shelf_candidates (
			book_id INTEGER NOT NULL,
			shelf_id TEXT NOT NULL,
			confidence REAL NOT NULL DEFAULT 0,
			observations INTEGER NOT NULL DEFAULT 0,
			last_seen_at TEXT NOT NULL DEFAULT '',
			PRIMARY KEY (book_id, shelf_id)
		)`,
	}
	for _, stmt := range stmts {
		if _, err := d.Exec(stmt); err != nil {
			return err
		}
	}
	return nil
}
