package db_test

import (
	"database/sql"
	"path/filepath"
	"testing"

	"booksearch/backend/internal/db"
)

// setupDB は空の SQLite DB にスキーマを作成してシードデータを投入し Store を返す。
func setupDB(t *testing.T) *db.Store {
	t.Helper()
	dir := t.TempDir()
	dbPath := filepath.Join(dir, "test.db")

	raw, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatalf("sql.Open: %v", err)
	}
	if err := db.CreateSchema(raw); err != nil {
		t.Fatalf("CreateSchema: %v", err)
	}

	// book 1: Go
	raw.Exec(
		`INSERT INTO books (title, authors, publisher, isbn, title_norm, authors_norm, isbn_norm)
		 VALUES (?, ?, ?, ?, ?, ?, ?)`,
		"Go言語プログラミング", "山田太郎", "技術書院", "9781234567890",
		"go言語プログラミング", "山田太郎", "9781234567890",
	)
	raw.Exec(`INSERT INTO book_covers (book_id, thumbnail, info_link) VALUES (1, NULL, NULL)`)
	raw.Exec(
		`INSERT INTO book_shelf_candidates (book_id, shelf_id, confidence, observations, last_seen_at)
		 VALUES (?, ?, ?, ?, ?)`,
		1, "shelf-A-01", 0.81234, 3, "2026-06-29T00:00:00Z",
	)

	// book 2: Python
	raw.Exec(
		`INSERT INTO books (title, authors, publisher, isbn, title_norm, authors_norm, isbn_norm)
		 VALUES (?, ?, ?, ?, ?, ?, ?)`,
		"Pythonプログラミング入門", "佐藤花子", "プログラム出版", "9789876543210",
		"pythonプログラミング入門", "佐藤花子", "9789876543210",
	)
	raw.Exec(`INSERT INTO book_covers (book_id, thumbnail, info_link) VALUES (2, NULL, NULL)`)
	raw.Close()

	store, err := db.Open(dbPath)
	if err != nil {
		t.Fatalf("db.Open: %v", err)
	}
	t.Cleanup(func() { store.Close() })
	return store
}

// ── Search ──────────────────────────────────────────────────

func TestSearch_ByTitle(t *testing.T) {
	store := setupDB(t)

	books, err := store.Search("Go言語", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(books) != 1 {
		t.Fatalf("want 1, got %d", len(books))
	}
	if books[0].Title != "Go言語プログラミング" {
		t.Errorf("wrong title: %q", books[0].Title)
	}
}

func TestSearch_AttachesShelfCandidates(t *testing.T) {
	store := setupDB(t)

	books, err := store.Search("Go言語", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(books) != 1 {
		t.Fatalf("want 1, got %d", len(books))
	}
	if len(books[0].ShelfCandidates) != 1 {
		t.Fatalf("want 1 shelf candidate, got %d", len(books[0].ShelfCandidates))
	}
	if got := books[0].ShelfCandidates[0].ShelfID; got != "shelf-A-01" {
		t.Errorf("shelf_id: got %q", got)
	}
	if got := books[0].ShelfCandidates[0].Confidence; got != 0.8123 {
		t.Errorf("confidence: got %.4f", got)
	}
	if len(books[0].ShelfIDs) != 1 || books[0].ShelfIDs[0] != "shelf-A-01" {
		t.Errorf("shelf_ids: got %#v", books[0].ShelfIDs)
	}
}

func TestSearch_ByTitleCaseNormalized(t *testing.T) {
	store := setupDB(t)

	// title_norm は小文字。大文字クエリでも title_norm にマッチするか確認。
	books, err := store.Search("go言語", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(books) != 1 {
		t.Fatalf("want 1, got %d", len(books))
	}
}

func TestSearch_ByAuthor(t *testing.T) {
	store := setupDB(t)

	books, err := store.Search("佐藤", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(books) != 1 {
		t.Fatalf("want 1, got %d", len(books))
	}
	if books[0].Title != "Pythonプログラミング入門" {
		t.Errorf("wrong title: %q", books[0].Title)
	}
	if books[0].Authors != "佐藤花子" {
		t.Errorf("wrong authors: %q", books[0].Authors)
	}
}

func TestSearch_ByISBN(t *testing.T) {
	store := setupDB(t)

	books, err := store.Search("9789876543210", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(books) != 1 {
		t.Fatalf("want 1, got %d", len(books))
	}
	if books[0].ISBN != "9789876543210" {
		t.Errorf("wrong isbn: %q", books[0].ISBN)
	}
}

func TestSearch_Limit(t *testing.T) {
	store := setupDB(t)

	// 両方の本に "プログラミング" が含まれる (2件マッチ) → limit=1 で 1 件のみ返す
	books, err := store.Search("プログラミング", 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(books) != 1 {
		t.Errorf("want 1 (limited), got %d", len(books))
	}
}

func TestSearch_NoResults(t *testing.T) {
	store := setupDB(t)

	books, err := store.Search("xyznotexist", 10)
	if err != nil {
		t.Fatal(err)
	}
	// 空の場合は nil または空スライスのどちらでも許容
	if len(books) != 0 {
		t.Errorf("want 0, got %d", len(books))
	}
}

func TestSearch_EmptyQuery(t *testing.T) {
	store := setupDB(t)

	// 空クエリは空文字列 LIKE '%%' になるので全件マッチする
	books, err := store.Search("", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(books) != 2 {
		t.Errorf("empty query should match all 2 books, got %d", len(books))
	}
}

// ── GetByID ─────────────────────────────────────────────────

func TestGetByID_Found(t *testing.T) {
	store := setupDB(t)

	book, err := store.GetByID(1)
	if err != nil {
		t.Fatal(err)
	}
	if book == nil {
		t.Fatal("want book, got nil")
	}
	if book.Title != "Go言語プログラミング" {
		t.Errorf("wrong title: %q", book.Title)
	}
	if book.Authors != "山田太郎" {
		t.Errorf("wrong authors: %q", book.Authors)
	}
	if book.Publisher != "技術書院" {
		t.Errorf("wrong publisher: %q", book.Publisher)
	}
	if book.ISBN != "9781234567890" {
		t.Errorf("wrong isbn: %q", book.ISBN)
	}
}

func TestGetByID_SecondBook(t *testing.T) {
	store := setupDB(t)

	book, err := store.GetByID(2)
	if err != nil {
		t.Fatal(err)
	}
	if book == nil {
		t.Fatal("want book, got nil")
	}
	if book.Title != "Pythonプログラミング入門" {
		t.Errorf("wrong title: %q", book.Title)
	}
}

func TestGetByID_NotFound(t *testing.T) {
	store := setupDB(t)

	book, err := store.GetByID(9999)
	if err != nil {
		t.Fatal(err)
	}
	if book != nil {
		t.Errorf("want nil, got %+v", book)
	}
}
