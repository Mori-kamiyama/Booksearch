package db

import (
	"database/sql"
	"math"
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
	ShelfID      string  `json:"shelf_id"`
	Confidence   float64 `json:"confidence"`
	Observations int     `json:"observations"`
	LastSeenAt   string  `json:"last_seen_at"`
}

type ShelfCandidateRow struct {
	BookID       int     `json:"book_id"`
	Title        string  `json:"title"`
	ShelfID      string  `json:"shelf_id"`
	Confidence   float64 `json:"confidence"`
	Observations int     `json:"observations"`
	AvgScore     float64 `json:"avg_score"`
	UpdatedAt    string  `json:"updated_at"`
	CropImage    string  `json:"crop_image,omitempty"`
	CropURL      string  `json:"crop_url,omitempty"`
}

type Store struct {
	db *sql.DB
}

func Open(path string) (*Store, error) {
	d, err := sql.Open("sqlite", path+"?_pragma=journal_mode(WAL)&_pragma=foreign_keys(on)")
	if err != nil {
		return nil, err
	}
	if err := CreateSchema(d); err != nil {
		d.Close()
		return nil, err
	}
	return &Store{db: d}, nil
}

func (s *Store) Close() error { return s.db.Close() }

func (s *Store) Search(query string, limit int) ([]Book, error) {
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
	books, err := scanBooks(rows)
	if err != nil {
		return nil, err
	}
	return s.attachShelfCandidates(books)
}

func (s *Store) GetByID(id int) (*Book, error) {
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
	books, err = s.attachShelfCandidates(books)
	if err != nil {
		return nil, err
	}
	return &books[0], nil
}

// FeaturedBooks はサムネイルが登録済みの本からランダムにN件返す。
// トップページの「今週のおすすめ」用で、実在する書影のない本は対象外にする。
func (s *Store) FeaturedBooks(limit int) ([]Book, error) {
	if limit <= 0 {
		limit = 6
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
	books, err := scanBooks(rows)
	if err != nil {
		return nil, err
	}
	return s.attachShelfCandidates(books)
}

func (s *Store) ShelfCandidates(bookID int, limit int) ([]ShelfCandidate, error) {
	if limit <= 0 {
		limit = 5
	}
	rows, err := s.db.Query(`
		SELECT shelf_id, confidence, observations, last_seen_at
		FROM book_shelf_candidates
		WHERE book_id = ?
		ORDER BY confidence DESC, observations DESC, last_seen_at DESC
		LIMIT ?`, bookID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []ShelfCandidate
	for rows.Next() {
		var c ShelfCandidate
		if err := rows.Scan(&c.ShelfID, &c.Confidence, &c.Observations, &c.LastSeenAt); err != nil {
			return nil, err
		}
		c.Confidence = math.Round(c.Confidence*10000) / 10000
		out = append(out, c)
	}
	return out, rows.Err()
}

func (s *Store) AllShelfCandidates(limit int) ([]ShelfCandidateRow, error) {
	if limit <= 0 {
		limit = 500
	}
	rows, err := s.db.Query(`
		SELECT bsc.book_id, b.title, bsc.shelf_id, bsc.confidence,
		       bsc.observations, bsc.last_seen_at
		FROM book_shelf_candidates bsc
		JOIN books b ON b.id = bsc.book_id
		ORDER BY bsc.shelf_id ASC, bsc.confidence DESC, b.title ASC
		LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []ShelfCandidateRow{}
	for rows.Next() {
		var row ShelfCandidateRow
		if err := rows.Scan(
			&row.BookID, &row.Title, &row.ShelfID, &row.Confidence,
			&row.Observations, &row.UpdatedAt,
		); err != nil {
			return nil, err
		}
		row.Confidence = math.Round(row.Confidence*10000) / 10000
		if row.Observations > 0 {
			row.AvgScore = math.Round(row.Confidence/(1-math.Pow(0.72, float64(row.Observations)))*10000) / 10000
		}
		out = append(out, row)
	}
	return out, rows.Err()
}

func (s *Store) attachShelfCandidates(books []Book) ([]Book, error) {
	for i := range books {
		candidates, err := s.ShelfCandidates(books[i].ID, 5)
		if err != nil {
			return nil, err
		}
		books[i].ShelfCandidates = candidates
		if len(candidates) == 0 {
			continue
		}
		books[i].ShelfIDs = make([]string, 0, len(candidates))
		for _, c := range candidates {
			books[i].ShelfIDs = append(books[i].ShelfIDs, c.ShelfID)
		}
	}
	return books, nil
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
