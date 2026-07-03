package main

import (
	"database/sql"
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
	UpdatedAt    string  `json:"updated_at,omitempty"`
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
