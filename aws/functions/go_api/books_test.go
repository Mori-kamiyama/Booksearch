package main

import "testing"

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
