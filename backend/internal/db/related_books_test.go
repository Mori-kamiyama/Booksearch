package db

import (
	"database/sql"
	"path/filepath"
	"testing"
)

func TestRelatedBooksUsesOnlyValidPrecomputedCandidates(t *testing.T) {
	d, err := sql.Open("sqlite", filepath.Join(t.TempDir(), "books.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	for _, stmt := range []string{
		`CREATE TABLE books(id INTEGER PRIMARY KEY,title TEXT,authors TEXT,publisher TEXT,published_date TEXT,class_number TEXT,registration_number TEXT,isbn TEXT)`,
		`CREATE TABLE book_covers(book_id INTEGER,thumbnail TEXT,info_link TEXT)`,
		`CREATE TABLE book_shelf_candidates(book_id INTEGER,shelf_id TEXT,confidence REAL,observations INTEGER,last_seen_at TEXT)`,
		`INSERT INTO books(id,title) VALUES(1,'対象'),(2,'関連'),(3,'別方式')`,
	} {
		if _, err = d.Exec(stmt); err != nil {
			t.Fatal(err)
		}
	}
	s := &Store{db: d}
	got, err := s.RelatedBooks(1, 6)
	if err != nil || len(got) != 0 {
		t.Fatalf("missing table: %v %v", got, err)
	}
	for _, stmt := range []string{
		`CREATE TABLE book_recommendations(source_book_id INTEGER,recommended_book_id INTEGER,score REAL,strategy TEXT,reasons_json TEXT)`,
		`INSERT INTO book_recommendations VALUES(1,1,99,'bm25-keyword-v1','[]'),(1,999,98,'bm25-keyword-v1','[]'),(1,3,97,'unknown','[]'),(1,2,5,'bm25-keyword-v1','["同じ著者の作品","unsupported"]'),(1,2,4,'bm25-keyword-v1','[]'),(2,3,3,'bm25-keyword-v1','[]')`,
	} {
		if _, err = d.Exec(stmt); err != nil {
			t.Fatal(err)
		}
	}
	got, err = s.RelatedBooks(1, 6)
	if err != nil || len(got) != 1 || got[0].ID != 2 || len(got[0].Reasons) != 1 {
		t.Fatalf("invalid ranking: %+v %v", got, err)
	}
	if _, err = d.Exec(`UPDATE book_recommendations SET reasons_json='invalid'`); err != nil {
		t.Fatal(err)
	}
	got, err = s.RelatedBooks(1, 6)
	if err != nil || len(got) != 1 || len(got[0].Reasons) != 0 {
		t.Fatalf("invalid reasons: %+v %v", got, err)
	}
	d.Close()
	if _, err = s.RelatedBooks(1, 6); err == nil {
		t.Fatal("closed database must not silently appear empty")
	}
}
