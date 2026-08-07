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
