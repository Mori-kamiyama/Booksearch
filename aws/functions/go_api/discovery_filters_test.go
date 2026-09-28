package main

import (
	"database/sql"
	"net/url"
	"testing"
)

func TestDiscoveryFiltersBeforePagination(t *testing.T) {
	raw, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer raw.Close()
	raw.SetMaxOpenConns(1)
	statements := []string{
		`CREATE TABLE books(id INTEGER PRIMARY KEY,title TEXT,authors TEXT,publisher TEXT,published_date TEXT,class_number TEXT,registration_number TEXT,isbn TEXT,title_norm TEXT,authors_norm TEXT,isbn_norm TEXT)`,
		`CREATE TABLE book_covers(book_id INTEGER,thumbnail TEXT,info_link TEXT)`,
		`CREATE TABLE book_shelf_candidates(book_id INTEGER,shelf_id TEXT,confidence REAL,observations INTEGER,last_seen_at TEXT)`,
		`CREATE TABLE book_discovery(book_id INTEGER PRIMARY KEY,page_count INTEGER,level TEXT)`,
		`CREATE TABLE book_topics(book_id INTEGER,topic_id TEXT,evidence TEXT)`,
		`INSERT INTO books(id,title,authors,title_norm,authors_norm) VALUES(1,'C言語一','著者','c言語一','著者'),(2,'C言語二','著者','c言語二','著者'),(3,'別の本','別著者','別の本','別著者')`,
		`INSERT INTO book_discovery VALUES(1,150,'beginner'),(2,400,NULL),(3,NULL,NULL)`,
		`INSERT INTO book_topics VALUES(1,'c-language','title'),(2,'c-language','title'),(1,'ndc-0','classification'),(2,'ndc-5','classification')`,
	}
	for _, statement := range statements {
		if _, err = raw.Exec(statement); err != nil {
			t.Fatal(err)
		}
	}
	store := &BookStore{db: raw}
	result, err := store.SearchFiltered("", 1, 1, SearchFilters{Topic: "c-language", Author: "著者"})
	if err != nil {
		t.Fatal(err)
	}
	if result.Total != 2 || len(result.Books) != 1 {
		t.Fatalf("%+v", result)
	}
	result, err = store.SearchFiltered("", 10, 0, SearchFilters{Genre: "ndc-0", Topic: "c-language"})
	if err != nil || result.Total != 1 || result.Books[0].ID != 1 {
		t.Fatalf("genre intersection: %+v %v", result, err)
	}
	result, err = store.SearchFiltered("", 10, 0, SearchFilters{MaxPages: 200, Level: "beginner"})
	if err != nil || result.Total != 1 || result.Books[0].ID != 1 {
		t.Fatalf("%+v %v", result, err)
	}
	result, err = store.SearchFiltered("", 10, 0, SearchFilters{Topic: "' OR 1=1 --"})
	if err != nil || result.Total != 0 {
		t.Fatalf("injection: %+v %v", result, err)
	}
	if _, err = ParseSearchFilters(url.Values{"min_pages": {"300"}, "max_pages": {"100"}}); err == nil {
		t.Fatal("accepted inverted range")
	}
	if _, err = ParseSearchFilters(url.Values{"level": {"guessed"}}); err == nil {
		t.Fatal("accepted invalid level")
	}
	for _, stmt := range []string{
		`CREATE TABLE book_search_terms(book_id INTEGER,value_norm TEXT,PRIMARY KEY(book_id,value_norm))`,
		`INSERT INTO book_search_terms VALUES(1,'しーげんご'),(2,'しーげんご'),(1,'ぱいそん')`,
	} {
		if _, err = raw.Exec(stmt); err != nil {
			t.Fatal(err)
		}
	}
	result, err = store.SearchFiltered("シーゲンゴ", 1, 1, SearchFilters{})
	if err != nil || result.Total != 2 || len(result.Books) != 1 {
		t.Fatalf("reading pagination: %+v %v", result, err)
	}
	result, err = store.SearchFiltered("シーゲンゴ 著者", 10, 0, SearchFilters{MaxPages: 200})
	if err != nil || result.Total != 1 || result.Books[0].ID != 1 {
		t.Fatalf("reading AND/filter: %+v %v", result, err)
	}
	result, err = store.SearchFiltered("パイソン", 10, 0, SearchFilters{})
	if err != nil || result.Total != 1 {
		t.Fatalf("alias: %+v %v", result, err)
	}
	result, err = store.SearchFiltered("ぱい%そん", 10, 0, SearchFilters{})
	if err != nil || result.Total != 0 {
		t.Fatalf("escaped alias: %+v %v", result, err)
	}
	raw.Exec("DROP TABLE book_topics")
	if _, err = store.SearchFiltered("", 10, 0, SearchFilters{Topic: "c-language"}); err == nil {
		t.Fatal("silently ignored unavailable filter")
	}
}
