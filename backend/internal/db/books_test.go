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
	wantThumbnail := "https://books.google.com/books/content?vid=ISBN9781234567890&printsec=frontcover&img=1&zoom=1&source=gbs_api"
	if books[0].Thumbnail == nil || *books[0].Thumbnail != wantThumbnail {
		t.Errorf("thumbnail: got %v, want %q", books[0].Thumbnail, wantThumbnail)
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

	books, err := store.Search("", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(books) != 0 {
		t.Errorf("empty query should match no books, got %d", len(books))
	}
}

func TestSearch_SplitsTermsAndEscapesWildcards(t *testing.T) {
	store := setupDB(t)

	books, err := store.Search("Go言語 プログラミング", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(books) != 1 || books[0].ID != 1 {
		t.Fatalf("AND search got %#v, want book 1", books)
	}
	for _, query := range []string{"%", "_", "!!!"} {
		books, err := store.Search(query, 10)
		if err != nil {
			t.Fatal(err)
		}
		if len(books) != 0 {
			t.Errorf("query %q should not match all books, got %d", query, len(books))
		}
	}
}

func TestSearch_ISBNNormalizationAndTotal(t *testing.T) {
	store := setupDB(t)

	result, err := store.SearchWithTotal("978-987-654-3210", 1)
	if err != nil {
		t.Fatal(err)
	}
	if result.Total != 1 || len(result.Books) != 1 || result.Books[0].ID != 2 {
		t.Fatalf("ISBN search got total=%d books=%#v, want total=1/book 2", result.Total, result.Books)
	}

	first, err := store.SearchWithTotal("プログラミング", 1)
	if err != nil {
		t.Fatal(err)
	}
	second, err := store.SearchWithTotal("プログラミング", 1)
	if err != nil {
		t.Fatal(err)
	}
	if first.Total != 2 || len(first.Books) != 1 || len(second.Books) != 1 || first.Books[0].ID != second.Books[0].ID {
		t.Fatalf("stable limited search got first=%#v second=%#v", first, second)
	}
}

func TestSearchOffsetKeepsStableTotal(t *testing.T) {
	store := setupDB(t)

	first, err := store.SearchWithTotalOffset("プログラミング", 1, 0)
	if err != nil {
		t.Fatal(err)
	}
	second, err := store.SearchWithTotalOffset("プログラミング", 1, 1)
	if err != nil {
		t.Fatal(err)
	}
	if first.Total != 2 || second.Total != 2 || len(first.Books) != 1 || len(second.Books) != 1 {
		t.Fatalf("offset result totals/books: first=%#v second=%#v", first, second)
	}
	if first.Books[0].ID == second.Books[0].ID {
		t.Fatalf("offset returned the same book: first=%d second=%d", first.Books[0].ID, second.Books[0].ID)
	}
}

func TestSearchOffsetBoundsDoNotOverflow(t *testing.T) {
	store := setupDB(t)

	negative, err := store.SearchWithTotalOffset("プログラミング", 1, -1)
	if err != nil {
		t.Fatalf("negative offset: %v", err)
	}
	if len(negative.Books) != 1 || negative.Books[0].ID != 1 {
		t.Fatalf("negative offset should clamp to first page: %#v", negative.Books)
	}

	maxInt := int(^uint(0) >> 1)
	extreme, err := store.SearchWithTotalOffset("プログラミング", 1, maxInt)
	if err != nil {
		t.Fatalf("maximum offset should be safe: %v", err)
	}
	if extreme.Total != 2 || len(extreme.Books) != 0 {
		t.Fatalf("maximum offset should return an empty page with stable total: %#v", extreme)
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
