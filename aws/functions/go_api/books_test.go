package main

import (
	"fmt"
	"strings"
	"testing"
	"unicode/utf8"
)

func TestFeaturedReturnsBooksWithCovers(t *testing.T) {
	store, err := OpenBookStore("library.db")
	if err != nil {
		t.Fatalf("open library DB: %v", err)
	}
	defer store.db.Close()

	books, err := store.Featured(5)
	if err != nil {
		t.Fatalf("featured books: %v", err)
	}
	if len(books) != 5 {
		t.Fatalf("got %d books, want 5", len(books))
	}
	for _, book := range books {
		if book.Thumbnail == nil || *book.Thumbnail == "" {
			t.Fatalf("book %d (%s) has no cover", book.ID, book.Title)
		}
	}
}

func TestFeaturedIsStableWithinWeek(t *testing.T) {
	store, err := OpenBookStore("library.db")
	if err != nil {
		t.Fatalf("open library DB: %v", err)
	}
	defer store.db.Close()

	first, err := store.Featured(5)
	if err != nil {
		t.Fatalf("first featured books: %v", err)
	}
	if err := store.db.Close(); err != nil {
		t.Fatalf("close DB after cache fill: %v", err)
	}
	second, err := store.Featured(5)
	if err != nil {
		t.Fatalf("second featured books: %v", err)
	}
	if len(first) != len(second) {
		t.Fatalf("featured result lengths differ: %d vs %d", len(first), len(second))
	}
	for i := range first {
		if first[i].ID != second[i].ID {
			t.Fatalf("featured order changed at %d: %d vs %d", i, first[i].ID, second[i].ID)
		}
	}
}

func TestSearchEscapesLikeWildcards(t *testing.T) {
	store, err := OpenBookStore("library.db")
	if err != nil {
		t.Fatalf("open library DB: %v", err)
	}
	defer store.db.Close()

	result, err := store.SearchWithTotal("%", 5)
	if err != nil {
		t.Fatalf("search literal wildcard: %v", err)
	}
	if result.Total != 0 || len(result.Books) != 0 {
		t.Fatalf("wildcard-only search matched books: total=%d books=%d", result.Total, len(result.Books))
	}
}

func TestIndexEntriesIncludeTitles(t *testing.T) {
	store, err := OpenBookStore("library.db")
	if err != nil {
		t.Fatalf("open library DB: %v", err)
	}
	defer store.db.Close()

	entries, err := store.IndexEntries([]int{1, 1, 2})
	if err != nil {
		t.Fatalf("index entries: %v", err)
	}
	if len(entries) == 0 {
		t.Fatal("expected index entries")
	}
	for id, entry := range entries {
		if entry.Title == "" {
			t.Fatalf("book %d has no title", id)
		}
		if entry.TitleReading == "" {
			t.Fatalf("book %d has no title reading", id)
		}
	}
}

func TestTitleReadingUsesMorphologicalAnalysis(t *testing.T) {
	tests := map[string]string{
		"老人と海":     "ロウジントウミ",
		"自然言語処理":   "シゼンゲンゴショリ",
		"Apple入門":  "Appleニュウモン",
		"『図書館の本』":  "トショカンノホン",
		"耐量子計算機暗号": "タイリョウシケイサンキアンゴウ",
		"3ステップで学ぶ": "サンステップデマナブ",
	}
	for title, want := range tests {
		if got := titleReading(title); got != want {
			t.Errorf("titleReading(%q) = %q, want %q", title, got, want)
		}
	}
}

func TestAllIndexBooksIncludesEntireCatalog(t *testing.T) {
	store, err := OpenBookStore("library.db")
	if err != nil {
		t.Fatalf("open library DB: %v", err)
	}
	defer store.db.Close()

	books, err := store.AllIndexBooks()
	if err != nil {
		t.Fatalf("all index books: %v", err)
	}
	if len(books) != 4202 {
		t.Fatalf("got %d index books, want 4202", len(books))
	}
	var unsupported []string
	for _, book := range books {
		if book.ID <= 0 || book.Title == "" || book.TitleReading == "" {
			t.Fatalf("incomplete index book: %+v", book)
		}
		first, _ := utf8.DecodeRuneInString(strings.TrimSpace(book.TitleReading))
		if !((first >= 'ぁ' && first <= 'ゖ') || (first >= 'ァ' && first <= 'ヶ') || (first >= 'A' && first <= 'Z') || (first >= 'a' && first <= 'z')) {
			unsupported = append(unsupported, fmt.Sprintf("%d:%s => %s", book.ID, book.Title, book.TitleReading))
		}
	}
	if len(unsupported) > 0 {
		limit := min(len(unsupported), 30)
		t.Fatalf("%d books would fall into the catch-all group:\n%s", len(unsupported), strings.Join(unsupported[:limit], "\n"))
	}
}
