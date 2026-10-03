package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func createSchemaForTest(raw *sql.DB) error {
	for _, q := range []string{
		`CREATE TABLE books(id INTEGER PRIMARY KEY,title TEXT,authors TEXT,publisher TEXT,published_date TEXT,class_number TEXT,registration_number TEXT,isbn TEXT,title_norm TEXT,authors_norm TEXT,isbn_norm TEXT)`,
		`CREATE TABLE book_covers(book_id INTEGER,thumbnail TEXT,info_link TEXT)`,
	} {
		if _, err := raw.Exec(q); err != nil {
			return err
		}
	}
	return nil
}

type semanticTestEmbedder struct {
	calls  int
	vector []float64
	err    error
}

func (p *semanticTestEmbedder) Embed(ctx context.Context, query string) ([]float64, error) {
	p.calls++
	return append([]float64{}, p.vector...), p.err
}

func TestSemanticKeepsFiltersAndExcludesBeforeEmbedding(t *testing.T) {
	raw, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer raw.Close()
	raw.SetMaxOpenConns(1)
	if err = createSchemaForTest(raw); err != nil {
		t.Fatal(err)
	}
	for _, query := range []string{
		`CREATE TABLE book_discovery(book_id INTEGER,page_count INTEGER,level TEXT)`,
		`CREATE TABLE book_topics(book_id INTEGER,topic_id TEXT)`,
		`INSERT INTO books(id,title,authors,authors_norm) VALUES(1,'本一','著者','著者'),(2,'本二','著者','著者'),(3,'本三','別著者','別著者'),(4,'本四','著者','著者')`,
		`INSERT INTO book_discovery VALUES(1,120,'beginner'),(2,400,'beginner'),(3,100,'beginner'),(4,NULL,NULL)`,
		`INSERT INTO book_topics VALUES(1,'topic'),(2,'topic'),(3,'other'),(4,'topic')`,
	} {
		if _, err = raw.Exec(query); err != nil {
			t.Fatal(err)
		}
	}
	store := &BookStore{db: raw}
	provider := &semanticTestEmbedder{vector: []float64{1, 0}}
	index := &SemanticIndex{Embedder: provider, gate: make(chan struct{}, 1), Books: []SemanticVector{
		{1, []float64{0.8, 0.6}}, {2, []float64{1, 0}}, {3, []float64{1, 0}}, {4, []float64{1, 0}},
	}}
	filters := SearchFilters{Author: "著者", Topic: "topic", MinPages: 100, MaxPages: 200, Level: "beginner"}
	books, err := store.SearchSemantic(context.Background(), index, "著者", filters, nil)
	if err != nil || len(books) != 1 || books[0].ID != 1 {
		t.Fatalf("filter leakage: %+v %v", books, err)
	}
	books, err = store.SearchSemantic(context.Background(), index, "著者", filters, map[int]bool{1: true})
	if err != nil || len(books) != 0 || provider.calls != 1 {
		t.Fatalf("empty candidates called model: %+v %v calls=%d", books, err, provider.calls)
	}
	if _, err = raw.Exec("DROP TABLE book_topics"); err != nil {
		t.Fatal(err)
	}
	if _, err = store.SearchSemantic(context.Background(), index, "著者", filters, nil); err == nil {
		t.Fatal("silently dropped filters")
	}
}

func TestSemanticFailureDoesNotChangeKeywordSearch(t *testing.T) {
	raw, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer raw.Close()
	if err = createSchemaForTest(raw); err != nil {
		t.Fatal(err)
	}
	if _, err = raw.Exec(`INSERT INTO books(id,title,title_norm) VALUES(1,'Python','python')`); err != nil {
		t.Fatal(err)
	}
	store := &BookStore{db: raw}
	provider := &semanticTestEmbedder{err: errors.New("provider failed")}
	index := &SemanticIndex{Embedder: provider, gate: make(chan struct{}, 1), Books: []SemanticVector{{1, []float64{1, 0}}}}
	if _, err = store.SearchSemantic(context.Background(), index, "Python", SearchFilters{}, nil); err == nil {
		t.Fatal("hid provider failure")
	}
	result, err := store.SearchFiltered("Python", 10, 0, SearchFilters{})
	if err != nil || result.Total != 1 {
		t.Fatalf("keyword search changed: %+v %v", result, err)
	}
	provider.err = nil
	provider.vector = []float64{1}
	if _, err = store.SearchSemantic(context.Background(), index, "Python", SearchFilters{}, nil); err == nil {
		t.Fatal("accepted wrong dimensions")
	}
	// A busy instance must reject another request without a second model call.
	index.gate <- struct{}{}
	before := provider.calls
	if _, err = store.SearchSemantic(context.Background(), index, "Python", SearchFilters{}, nil); err == nil || provider.calls != before {
		t.Fatal("busy search called provider")
	}
	<-index.gate
}

func TestSemanticIndexRejectsWrongCatalogAndInvalidVectors(t *testing.T) {
	dir := t.TempDir()
	catalog := filepath.Join(dir, "catalog.db")
	if err := os.WriteFile(catalog, []byte("snapshot"), 0600); err != nil {
		t.Fatal(err)
	}
	vector := make([]float64, 1024)
	vector[0] = 1
	index := SemanticIndex{Version: 1, Model: SemanticModel, CatalogSHA256: fmt.Sprintf("%x", sha256.Sum256([]byte("snapshot"))), Books: []SemanticVector{{1, vector}}}
	path := filepath.Join(dir, "index.json")
	save := func() {
		raw, err := json.Marshal(index)
		if err != nil {
			t.Fatal(err)
		}
		if err = os.WriteFile(path, raw, 0600); err != nil {
			t.Fatal(err)
		}
	}
	save()
	if _, err := LoadSemanticIndex(path, catalog); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(catalog, []byte("different"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadSemanticIndex(path, catalog); err == nil {
		t.Fatal("accepted stale catalog")
	}
	if err := os.WriteFile(catalog, []byte("snapshot"), 0600); err != nil {
		t.Fatal(err)
	}
	index.Books[0].Vector = make([]float64, 1024)
	save()
	if _, err := LoadSemanticIndex(path, catalog); err == nil {
		t.Fatal("accepted zero vector")
	}
	index.Books[0].Vector = vector
	index.Books = append(index.Books, index.Books[0])
	save()
	if _, err := LoadSemanticIndex(path, catalog); err == nil {
		t.Fatal("accepted duplicate IDs")
	}
}

func TestSemanticQueryGuards(t *testing.T) {
	for _, q := range []string{"", "???", "978-4-123456-78-9", "９７８４１２３４５６７８９", "ISBN: 9784123456789", "123456789X"} {
		if SemanticQueryAllowed(q, false) {
			t.Fatalf("allowed identifier/empty query %q", q)
		}
	}
	for _, q := range []string{"ユーザビリティ", "C++", "Python データ分析"} {
		if !SemanticQueryAllowed(q, false) || SemanticQueryAllowed(q, true) {
			t.Fatalf("wrong query guard %q", q)
		}
	}
}
