package main

import (
	"database/sql"
	"fmt"
	"hash/fnv"
	"sort"
	"strings"
	"sync"
	"time"
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

type searchTerm struct {
	normalized string
	isbn       string
}

type SearchResult struct {
	Books []Book
	Total int
}

func searchTerms(query string) []searchTerm {
	var terms []searchTerm
	for _, raw := range strings.Fields(norm.NFKC.String(query)) {
		term := normalizeQuery(raw)
		if term == "" || !hasSearchContent(term) {
			continue
		}
		isbn := normalizeISBN(raw)
		if len(isbn) < 10 {
			isbn = ""
		}
		terms = append(terms, searchTerm{normalized: term, isbn: isbn})
	}
	return terms
}

func hasSearchContent(value string) bool {
	for _, r := range value {
		if unicode.IsLetter(r) || unicode.IsNumber(r) {
			return true
		}
	}
	return false
}

func normalizeISBN(value string) string {
	var b strings.Builder
	for _, r := range value {
		if r >= '0' && r <= '9' {
			b.WriteRune(r)
		} else if r == 'x' || r == 'X' {
			b.WriteRune('X')
		}
	}
	return b.String()
}

func escapeLike(value string) string {
	return strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`).Replace(value)
}

func searchWhere(terms []searchTerm) (string, []any) {
	clauses := make([]string, 0, len(terms))
	args := make([]any, 0, len(terms)*3)
	for _, term := range terms {
		pattern := "%" + escapeLike(term.normalized) + "%"
		isbnPattern := pattern
		if term.isbn != "" {
			isbnPattern = "%" + escapeLike(term.isbn) + "%"
		}
		clauses = append(clauses, `(COALESCE(b.title_norm,'') LIKE ? ESCAPE '\' OR COALESCE(b.authors_norm,'') LIKE ? ESCAPE '\' OR COALESCE(b.isbn_norm,'') LIKE ? ESCAPE '\')`)
		args = append(args, pattern, pattern, isbnPattern)
	}
	return strings.Join(clauses, " AND "), args
}

func searchOrder(terms []searchTerm) (string, []any) {
	joined := make([]string, 0, len(terms))
	for _, term := range terms {
		joined = append(joined, term.normalized)
	}
	full := strings.Join(joined, "")
	first := joined[0]
	order := `CASE `
	args := make([]any, 0, 6)
	if len(terms) == 1 && terms[0].isbn != "" {
		order += `WHEN LOWER(b.isbn_norm) = LOWER(?) THEN 500 `
		args = append(args, terms[0].isbn)
	}
	order += `WHEN b.title_norm = ? THEN 400 `
	args = append(args, full)
	order += `WHEN b.title_norm LIKE ? ESCAPE '\' THEN 300 `
	args = append(args, escapeLike(first)+"%")
	order += `WHEN b.authors_norm LIKE ? ESCAPE '\' THEN 200 `
	args = append(args, escapeLike(first)+"%")
	order += `WHEN b.title_norm LIKE ? ESCAPE '\' THEN 100 `
	args = append(args, "%"+escapeLike(full)+"%")
	order += `WHEN b.authors_norm LIKE ? ESCAPE '\' THEN 50 ELSE 0 END DESC, b.title_norm COLLATE NOCASE ASC, b.id ASC`
	args = append(args, "%"+escapeLike(full)+"%")
	return order, args
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
	db            *sql.DB
	featuredMu    sync.Mutex
	featuredWeek  string
	featuredBooks []Book
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
	result, err := s.SearchWithTotal(query, limit)
	return result.Books, err
}

func (s *BookStore) SearchWithTotal(query string, limit int) (SearchResult, error) {
	terms := searchTerms(query)
	if len(terms) == 0 {
		return SearchResult{Books: []Book{}, Total: 0}, nil
	}
	if limit <= 0 {
		limit = 20
	}
	where, whereArgs := searchWhere(terms)
	var total int
	if err := s.db.QueryRow("SELECT COUNT(DISTINCT b.id) FROM books b WHERE "+where, whereArgs...).Scan(&total); err != nil {
		return SearchResult{}, err
	}
	order, orderArgs := searchOrder(terms)
	args := append(append([]any{}, whereArgs...), orderArgs...)
	args = append(args, limit)
	rows, err := s.db.Query(`
		SELECT b.id, b.title, COALESCE(b.authors,''), COALESCE(b.publisher,''),
		       COALESCE(b.published_date,''), COALESCE(b.class_number,''),
		       COALESCE(b.registration_number,''), COALESCE(b.isbn,''),
		       bc.thumbnail, bc.info_link
		FROM books b
		LEFT JOIN book_covers bc ON b.id = bc.book_id
		WHERE `+where+` ORDER BY `+order+` LIMIT ?`, args...,
	)
	if err != nil {
		return SearchResult{}, err
	}
	defer rows.Close()
	books, err := scanBooks(rows)
	if err != nil {
		return SearchResult{}, err
	}
	return SearchResult{Books: books, Total: total}, nil
}

func (s *BookStore) Featured(limit int) ([]Book, error) {
	if limit <= 0 {
		limit = 5
	}
	if limit > 20 {
		limit = 20
	}
	weekKey := featuredWeekKey()
	s.featuredMu.Lock()
	if s.featuredWeek == weekKey {
		books := cloneBooks(s.featuredBooks)
		s.featuredMu.Unlock()
		if len(books) > limit {
			books = books[:limit]
		}
		return books, nil
	}
	defer s.featuredMu.Unlock()
	rows, err := s.db.Query(`
		SELECT b.id, b.title, COALESCE(b.authors,''), COALESCE(b.publisher,''),
		       COALESCE(b.published_date,''), COALESCE(b.class_number,''),
		       COALESCE(b.registration_number,''), COALESCE(b.isbn,''),
		       bc.thumbnail, bc.info_link
		FROM books b
		JOIN book_covers bc ON b.id = bc.book_id
		WHERE bc.thumbnail IS NOT NULL AND bc.thumbnail != ''
		ORDER BY b.id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	books, err := scanBooks(rows)
	if err != nil {
		return nil, err
	}
	seed := weekKey
	sort.SliceStable(books, func(i, j int) bool {
		return featuredSortKey(seed, books[i].ID) < featuredSortKey(seed, books[j].ID)
	})
	s.featuredWeek = weekKey
	s.featuredBooks = cloneBooks(books)
	books = cloneBooks(books)
	if len(books) > limit {
		books = books[:limit]
	}
	return books, nil
}

func featuredWeekKey() string {
	year, week := time.Now().UTC().ISOWeek()
	return fmt.Sprintf("%d-%d", year, week)
}

func cloneBooks(books []Book) []Book {
	cloned := make([]Book, len(books))
	copy(cloned, books)
	return cloned
}

func featuredSortKey(seed string, id int) uint64 {
	h := fnv.New64a()
	_, _ = h.Write([]byte(fmt.Sprintf("%s:%d", seed, id)))
	return h.Sum64()
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
