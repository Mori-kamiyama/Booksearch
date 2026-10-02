package main

import (
	"database/sql"
	"testing"
)

func TestBookMetadataMissingAndMatched(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	store := &BookStore{db: db}
	book := &Book{ID: 1}
	if err := store.enrichBook(book); err != nil {
		t.Fatal(err)
	}
	for _, stmt := range []string{
		`CREATE TABLE book_discovery(book_id INTEGER,page_count INTEGER,level TEXT)`,
		`CREATE TABLE book_metadata(book_id INTEGER,description TEXT,source TEXT,fetch_status TEXT)`,
		`INSERT INTO book_discovery VALUES(1,282,NULL)`,
		`INSERT INTO book_metadata VALUES(1,'unverified','google_books','unmatched')`,
	} {
		if _, err := db.Exec(stmt); err != nil {
			t.Fatal(err)
		}
	}
	if err := store.enrichBook(book); err != nil {
		t.Fatal(err)
	}
	if book.Description != nil || book.PageCount == nil || *book.PageCount != 282 {
		t.Fatalf("%+v", book)
	}
	db.Exec(`UPDATE book_metadata SET description='verified',fetch_status='matched'`)
	if err := store.enrichBook(book); err != nil {
		t.Fatal(err)
	}
	if book.Description == nil || *book.Description != "verified" || *book.DescriptionSource != "google_books" {
		t.Fatalf("%+v", book)
	}
}
