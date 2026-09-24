package main

import (
	"database/sql"
	"fmt"
	"strings"
	"unicode"

	"golang.org/x/text/unicode/norm"
	_ "modernc.org/sqlite"
)

// normalizeQuery mirrors src/lookup.py's normalize_text(): NFKC-normalize,
// lowercase, then strip whitespace and punctuation so search queries match
// against title_norm/authors_norm columns built by the same normalization.
func normalizeQuery(value string) string {
	text := strings.ToLower(norm.NFKC.String(value))
	var b strings.Builder
	b.Grow(len(text))
	for _, r := range text {
		if isStrippedPunct(r) {
			continue
		}
		b.WriteRune(r)
	}
	return b.String()
}

func isStrippedPunct(r rune) bool {
	if unicode.IsSpace(r) {
		return true
	}
	switch r {
	case '　', '・', ':', '：', ',', '，', '.', '．', '。',
		'『', '』', '「', '」', '"', '\'', '“', '”', '‘', '’',
		'!', '?', '！', '？', '-', '‐', '‑', '‒', '–', '—', '―',
		'（', '）', '(', ')', '【', '】', '[', ']':
		return true
	}
	return false
}

type Book struct {
	ID                 int              `json:"id"`
	Title              string           `json:"title"`
	Authors            string           `json:"authors"`
	Publisher          string           `json:"publisher"`
	PublishedDate      string           `json:"published_date"`
	ClassNumber        string           `json:"class_number"`
	RegistrationNumber string           `json:"registration_number"`
	ISBN               string           `json:"isbn"`
	Thumbnail          *string          `json:"thumbnail"`
	InfoLink           *string          `json:"info_link"`
	ShelfIDs           []string         `json:"shelf_ids,omitempty"`
	ShelfCandidates    []ShelfCandidate `json:"shelf_candidates,omitempty"`
}

type ShelfCandidate struct {
	BookID       int     `json:"book_id"`
	ShelfID      string  `json:"shelf_id"`
	Confidence   float64 `json:"confidence"`
	Observations int     `json:"observations"`
	AvgScore     float64 `json:"avg_score"`
	Title        string  `json:"title,omitempty"`
	TitleReading string  `json:"title_reading,omitempty"`
	Thumbnail    *string `json:"thumbnail,omitempty"`
	UpdatedAt    string  `json:"updated_at,omitempty"`
}

type BookIndexEntry struct {
	Title        string
	TitleReading string
	Thumbnail    *string
}

type BookIndexBook struct {
	ID           int     `json:"id"`
	Title        string  `json:"title"`
	TitleReading string  `json:"title_reading"`
	Thumbnail    *string `json:"thumbnail"`
}

type BookStore struct {
	db *sql.DB
}

func OpenBookStore(path string) (*BookStore, error) {
	dsn := "file:" + path + "?mode=ro&immutable=1&_pragma=journal_mode(off)"
	d, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	if err := d.Ping(); err != nil {
		d.Close()
		return nil, err
	}
	return &BookStore{db: d}, nil
}

func (s *BookStore) Search(query string, limit int) ([]Book, error) {
	q := "%" + normalizeQuery(strings.TrimSpace(query)) + "%"
	rows, err := s.db.Query(`
		SELECT b.id, b.title, COALESCE(b.authors,''), COALESCE(b.publisher,''),
		       COALESCE(b.published_date,''), COALESCE(b.class_number,''),
		       COALESCE(b.registration_number,''), COALESCE(b.isbn,''),
		       bc.thumbnail, bc.info_link
		FROM books b
		LEFT JOIN book_covers bc ON b.id = bc.book_id
		WHERE b.title_norm LIKE ?
		   OR b.authors_norm LIKE ?
		   OR b.isbn_norm = ?
		LIMIT ?`,
		q, q, strings.TrimSpace(query), limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanBooks(rows)
}

func (s *BookStore) Featured(limit int) ([]Book, error) {
	if limit <= 0 {
		limit = 5
	}
	if limit > 20 {
		limit = 20
	}
	rows, err := s.db.Query(`
		SELECT b.id, b.title, COALESCE(b.authors,''), COALESCE(b.publisher,''),
		       COALESCE(b.published_date,''), COALESCE(b.class_number,''),
		       COALESCE(b.registration_number,''), COALESCE(b.isbn,''),
		       bc.thumbnail, bc.info_link
		FROM books b
		JOIN book_covers bc ON b.id = bc.book_id
		WHERE bc.thumbnail IS NOT NULL AND bc.thumbnail != ''
		ORDER BY RANDOM()
		LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanBooks(rows)
}

func (s *BookStore) GetByID(id int) (*Book, error) {
	rows, err := s.db.Query(`
		SELECT b.id, b.title, COALESCE(b.authors,''), COALESCE(b.publisher,''),
		       COALESCE(b.published_date,''), COALESCE(b.class_number,''),
		       COALESCE(b.registration_number,''), COALESCE(b.isbn,''),
		       bc.thumbnail, bc.info_link
		FROM books b
		LEFT JOIN book_covers bc ON b.id = bc.book_id
		WHERE b.id = ?`, id,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	books, err := scanBooks(rows)
	if err != nil || len(books) == 0 {
		return nil, err
	}
	return &books[0], nil
}

func (s *BookStore) IndexEntries(ids []int) (map[int]BookIndexEntry, error) {
	entries := make(map[int]BookIndexEntry)
	unique := make([]int, 0, len(ids))
	seen := make(map[int]struct{}, len(ids))
	for _, id := range ids {
		if id <= 0 {
			continue
		}
		if _, exists := seen[id]; exists {
			continue
		}
		seen[id] = struct{}{}
		unique = append(unique, id)
	}
	if len(unique) == 0 {
		return entries, nil
	}
	placeholders := strings.TrimSuffix(strings.Repeat("?,", len(unique)), ",")
	args := make([]any, len(unique))
	for index, id := range unique {
		args[index] = id
	}
	rows, err := s.db.Query(fmt.Sprintf(`
		SELECT b.id, b.title, bc.thumbnail
		FROM books b
		LEFT JOIN book_covers bc ON bc.book_id = b.id
		WHERE b.id IN (%s)`, placeholders), args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var id int
		var entry BookIndexEntry
		if err := rows.Scan(&id, &entry.Title, &entry.Thumbnail); err != nil {
			return nil, err
		}
		entry.TitleReading = titleReading(entry.Title)
		entries[id] = entry
	}
	return entries, rows.Err()
}

func (s *BookStore) AllIndexBooks() ([]BookIndexBook, error) {
	rows, err := s.db.Query(`
		SELECT b.id, b.title, bc.thumbnail
		FROM books b
		LEFT JOIN book_covers bc ON bc.book_id = b.id
		WHERE b.title IS NOT NULL AND b.title != ''
		ORDER BY b.id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	books := make([]BookIndexBook, 0, 4096)
	for rows.Next() {
		var book BookIndexBook
		if err := rows.Scan(&book.ID, &book.Title, &book.Thumbnail); err != nil {
			return nil, err
		}
		book.TitleReading = titleReading(book.Title)
		books = append(books, book)
	}
	return books, rows.Err()
}

func scanBooks(rows *sql.Rows) ([]Book, error) {
	var books []Book
	for rows.Next() {
		var b Book
		if err := rows.Scan(
			&b.ID, &b.Title, &b.Authors, &b.Publisher,
			&b.PublishedDate, &b.ClassNumber, &b.RegistrationNumber, &b.ISBN,
			&b.Thumbnail, &b.InfoLink,
		); err != nil {
			return nil, err
		}
		books = append(books, b)
	}
	return books, rows.Err()
}
