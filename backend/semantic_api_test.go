package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"booksearch/backend/internal/db"
	"booksearch/backend/internal/handler"
)

type apiSemanticEmbedder struct {
	calls int
	err   error
}

func (p *apiSemanticEmbedder) Embed(context.Context, string) ([]float64, error) {
	p.calls++
	vector := make([]float64, 1024)
	vector[0] = 1
	return vector, p.err
}

func TestSemanticAPIAndSnapshotRestarts(t *testing.T) {
	dir := t.TempDir()
	seeded := openTestStore(t, dir)
	if err := seeded.Close(); err != nil {
		t.Fatal(err)
	}
	catalog := filepath.Join(dir, "test.db")
	raw, err := os.ReadFile(catalog)
	if err != nil {
		t.Fatal(err)
	}
	a := make([]float64, 1024)
	a[0] = 1
	b := make([]float64, 1024)
	b[1] = 1
	artifact := db.SemanticIndex{Version: 1, Model: db.SemanticModel, CatalogSHA256: fmt.Sprintf("%x", sha256.Sum256(raw)), Books: []db.SemanticVector{{ID: 1, Vector: a}, {ID: 2, Vector: b}}}
	encoded, err := json.Marshal(artifact)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "index.json")
	if err = os.WriteFile(path, encoded, 0600); err != nil {
		t.Fatal(err)
	}
	index, err := db.LoadSemanticIndex(path, catalog)
	if err != nil {
		t.Fatal(err)
	}
	provider := &apiSemanticEmbedder{}
	index.Embedder = provider
	store, err := db.OpenSnapshot(catalog)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	h := &handler.Handler{Store: store, Semantic: index}
	router := buildRouter(h)
	request := func(path string) *httptest.ResponseRecorder {
		response := httptest.NewRecorder()
		router.ServeHTTP(response, httptest.NewRequest(http.MethodGet, path, nil))
		return response
	}
	if response := request("/api/books/semantic/status"); response.Code != 200 || response.Body.String() != `{"available":true}` {
		t.Fatalf("availability: %s", response.Body)
	}
	response := request("/api/books/semantic?q=design&exclude=1")
	var result struct {
		Books []db.Book `json:"books"`
	}
	if err = json.Unmarshal(response.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if response.Code != 200 || len(result.Books) != 1 || result.Books[0].ID != 2 {
		t.Fatalf("semantic response: %d %s", response.Code, response.Body)
	}
	for _, url := range []string{
		"/api/books/semantic?q=9784123456789", "/api/books/semantic?q=design&exact=1",
		"/api/books/semantic?q=design&max_pages=abc", "/api/books/semantic?q=design&exclude=no",
	} {
		if response := request(url); response.Code != 400 {
			t.Fatalf("invalid request accepted %s: %d", url, response.Code)
		}
	}
	if provider.calls != 1 {
		t.Fatalf("invalid queries called model: %d", provider.calls)
	}
	provider.err = errors.New("private provider failure")
	if response := request("/api/books/semantic?q=design"); response.Code != 503 {
		t.Fatalf("provider failure: %d", response.Code)
	}
	if response := request("/api/books/search?q=Go"); response.Code != 200 {
		t.Fatalf("keyword search broken: %d", response.Code)
	}
	h.Semantic = nil
	if response := request("/api/books/semantic/status"); response.Body.String() != `{"available":false}` {
		t.Fatalf("disabled availability: %s", response.Body)
	}
	if response := request("/api/books/semantic?q=design"); response.Code != 503 {
		t.Fatalf("disabled endpoint: %d", response.Code)
	}
	if err = store.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err = db.LoadSemanticIndex(path, catalog); err != nil {
		t.Fatalf("read-only serving invalidated snapshot: %v", err)
	}
}
