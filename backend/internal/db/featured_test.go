package db

import (
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func makeFeaturedStore(t *testing.T) (*Store, string) {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "library.db")
	raw, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	if err := CreateSchema(raw); err != nil {
		raw.Close()
		t.Fatal(err)
	}
	if _, err := raw.Exec(`INSERT INTO books (title, title_norm) VALUES ('実在する本', '実在する本'), ('もう一冊', 'もう一冊')`); err != nil {
		raw.Close()
		t.Fatal(err)
	}
	if _, err := raw.Exec(`INSERT INTO book_covers (book_id, thumbnail) VALUES (1, 'cover-1'), (2, 'cover-2')`); err != nil {
		raw.Close()
		t.Fatal(err)
	}
	if err := raw.Close(); err != nil {
		t.Fatal(err)
	}
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { store.Close() })
	return store, path
}

func TestFeaturedCacheSurvivesRestartAndWeekChange(t *testing.T) {
	oldNow := featuredNow
	defer func() { featuredNow = oldNow }()
	featuredNow = func() time.Time { return time.Date(2026, 9, 21, 0, 0, 0, 0, time.UTC) }
	store, path := makeFeaturedStore(t)
	first, err := store.FeaturedBooks(2)
	if err != nil || len(first) != 2 {
		t.Fatalf("first featured=%#v err=%v", first, err)
	}
	for _, book := range first {
		if book.ID <= 0 {
			t.Fatalf("invalid persisted book ID: %+v", book)
		}
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}

	restarted, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	restartedBooks, err := restarted.FeaturedBooks(2)
	if err != nil {
		t.Fatal(err)
	}
	if restartedBooks[0].ID != first[0].ID || restartedBooks[1].ID != first[1].ID {
		t.Fatalf("restart changed featured order: first=%v restarted=%v", first, restartedBooks)
	}

	featuredNow = func() time.Time { return time.Date(2026, 9, 28, 0, 0, 0, 0, time.UTC) }
	if _, err := restarted.db.Exec(`DELETE FROM book_covers`); err != nil {
		t.Fatal(err)
	}
	fallback, err := restarted.FeaturedBooks(2)
	if err != nil {
		t.Fatal(err)
	}
	if fallback[0].ID != first[0].ID || fallback[1].ID != first[1].ID {
		t.Fatalf("empty new week discarded previous cache: first=%v fallback=%v", first, fallback)
	}
	data, err := os.ReadFile(path + ".featured.json")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(data), `"week":"2026-39"`) {
		t.Fatalf("empty update overwrote previous manifest: %s", data)
	}
	if err := restarted.db.Close(); err != nil {
		t.Fatal(err)
	}
	closedDBFallback, err := restarted.FeaturedBooks(2)
	if err != nil {
		t.Fatal(err)
	}
	if closedDBFallback[0].ID != first[0].ID || closedDBFallback[1].ID != first[1].ID {
		t.Fatalf("DB/shelf fallback lost previous snapshot: first=%v fallback=%v", first, closedDBFallback)
	}
	if err := restarted.Close(); err != nil {
		t.Fatal(err)
	}
}
