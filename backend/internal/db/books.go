package db

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"hash/fnv"
	"math"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
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
	Thumbnail    *string `json:"thumbnail,omitempty"`
}

type Store struct {
	db                *sql.DB
	featuredMu        sync.Mutex
	featuredWeek      string
	featuredBooks     []Book
	featuredLastWeek  string
	featuredLastBooks []Book
	featuredCachePath string
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
	return &Store{db: d, featuredCachePath: path + ".featured.json"}, nil
}

func (s *Store) Close() error { return s.db.Close() }

func (s *Store) Search(query string, limit int) ([]Book, error) {
	result, err := s.SearchWithTotal(query, limit)
	return result.Books, err
}

func (s *Store) SearchWithTotal(query string, limit int) (SearchResult, error) {
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
	books, err = s.attachShelfCandidates(books)
	if err != nil {
		return SearchResult{}, err
	}
	return SearchResult{Books: books, Total: total}, nil
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

// FeaturedBooks はサムネイルが登録済みの本から週単位で固定したN件を返す。
// トップページの「今週のおすすめ」用で、実在する書影のない本は対象外にする。
func (s *Store) FeaturedBooks(limit int) ([]Book, error) {
	if limit <= 0 {
		limit = 6
	}
	if limit > 20 {
		limit = 20
	}
	weekKey := featuredWeekKey()
	s.featuredMu.Lock()
	defer s.featuredMu.Unlock()
	if s.featuredWeek == weekKey {
		books := cloneBooks(s.featuredBooks)
		if len(books) > limit {
			books = books[:limit]
		}
		return s.attachShelfCandidates(books)
	}
	if manifest, err := s.loadFeaturedManifest(); err == nil && manifest.Week == weekKey {
		s.setFeaturedSuccess(manifest)
		return s.attachShelfCandidates(truncateFeatured(manifest.Books, limit))
	} else if err == nil && manifest.Week != "" {
		s.setFeaturedLast(manifest)
	}
	previousWeek, previousBooks := s.featuredLastWeek, cloneBooks(s.featuredLastBooks)
	rows, err := s.db.Query(`
		SELECT b.id, b.title, COALESCE(b.authors,''), COALESCE(b.publisher,''),
		       COALESCE(b.published_date,''), COALESCE(b.class_number,''),
		       COALESCE(b.registration_number,''), COALESCE(b.isbn,''),
		       bc.thumbnail, bc.info_link
		FROM books b
		LEFT JOIN book_covers bc ON b.id = bc.book_id
		WHERE bc.thumbnail IS NOT NULL AND bc.thumbnail != ''
		ORDER BY b.id`)
	if err != nil {
		if previousWeek != "" {
			return s.featuredFallback(previousBooks, limit)
		}
		return nil, err
	}
	defer rows.Close()
	books, err := scanBooks(rows)
	if err != nil {
		if previousWeek != "" {
			return s.featuredFallback(previousBooks, limit)
		}
		return nil, err
	}
	seed := weekKey
	sort.SliceStable(books, func(i, j int) bool {
		return featuredSortKey(seed, books[i].ID) < featuredSortKey(seed, books[j].ID)
	})
	books = sanitizeFeaturedBooks(books)
	if len(books) == 0 {
		if previousWeek != "" {
			return s.featuredFallback(previousBooks, limit)
		}
		return []Book{}, nil
	}
	manifest := featuredManifest{Week: weekKey, Books: cloneBooks(books)}
	if err := s.saveFeaturedManifest(manifest); err != nil {
		if previousWeek != "" {
			return s.featuredFallback(previousBooks, limit)
		}
		// Leave this week uncached so a later request can retry the write.
		return s.attachShelfCandidates(truncateFeatured(manifest.Books, limit))
	}
	s.setFeaturedSuccess(manifest)
	return s.attachShelfCandidates(truncateFeatured(manifest.Books, limit))
}

type featuredManifest struct {
	Week  string `json:"week"`
	Books []Book `json:"books"`
}

func (s *Store) setFeaturedLast(manifest featuredManifest) {
	s.featuredLastWeek = manifest.Week
	s.featuredLastBooks = cloneBooks(manifest.Books)
}

func (s *Store) setFeaturedSuccess(manifest featuredManifest) {
	s.featuredWeek = manifest.Week
	s.featuredBooks = cloneBooks(manifest.Books)
	s.setFeaturedLast(manifest)
}

func truncateFeatured(books []Book, limit int) []Book {
	books = cloneBooks(books)
	if len(books) > limit {
		books = books[:limit]
	}
	return books
}

func sanitizeFeaturedBooks(books []Book) []Book {
	seen := make(map[int]struct{}, len(books))
	clean := make([]Book, 0, minInt(len(books), 20))
	for _, book := range books {
		if book.ID <= 0 {
			continue
		}
		if _, ok := seen[book.ID]; ok {
			continue
		}
		seen[book.ID] = struct{}{}
		clean = append(clean, book)
		if len(clean) == 20 {
			break
		}
	}
	return clean
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func (s *Store) featuredFallback(books []Book, limit int) ([]Book, error) {
	result := truncateFeatured(books, limit)
	enriched, err := s.attachShelfCandidates(result)
	if err != nil {
		// The weekly snapshot is still useful when the auxiliary shelf table is
		// temporarily unavailable. Keep the persisted book data visible.
		return result, nil
	}
	return enriched, nil
}

func (s *Store) loadFeaturedManifest() (featuredManifest, error) {
	data, err := os.ReadFile(s.featuredCachePath)
	if err != nil {
		return featuredManifest{}, err
	}
	var manifest featuredManifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return featuredManifest{}, err
	}
	if manifest.Week == "" || len(manifest.Books) == 0 || len(manifest.Books) > 20 {
		return featuredManifest{}, fmt.Errorf("invalid featured manifest book count")
	}
	if len(sanitizeFeaturedBooks(manifest.Books)) != len(manifest.Books) {
		return featuredManifest{}, fmt.Errorf("invalid featured manifest book IDs")
	}
	return manifest, nil
}

func (s *Store) saveFeaturedManifest(manifest featuredManifest) error {
	if manifest.Week == "" || len(manifest.Books) == 0 || len(manifest.Books) > 20 ||
		len(sanitizeFeaturedBooks(manifest.Books)) != len(manifest.Books) {
		return fmt.Errorf("invalid featured manifest")
	}
	data, err := json.Marshal(manifest)
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(s.featuredCachePath), ".booksearch-featured-")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if err := tmp.Chmod(0o644); err != nil {
		_ = tmp.Close()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, s.featuredCachePath)
}

var featuredNow = time.Now

func featuredWeekKey() string {
	year, week := featuredNow().UTC().ISOWeek()
	return strconv.Itoa(year) + "-" + strconv.Itoa(week)
}

func cloneBooks(books []Book) []Book {
	cloned := make([]Book, len(books))
	copy(cloned, books)
	return cloned
}

func featuredSortKey(seed string, id int) uint64 {
	h := fnv.New64a()
	_, _ = h.Write([]byte(seed + ":" + strconv.Itoa(id)))
	return h.Sum64()
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
		SELECT bsc.book_id, b.title, b.isbn, bsc.shelf_id, bsc.confidence,
		       bsc.observations, bsc.last_seen_at, bc.thumbnail
		FROM book_shelf_candidates bsc
		JOIN books b ON b.id = bsc.book_id
		LEFT JOIN book_covers bc ON b.id = bc.book_id
		ORDER BY bsc.shelf_id ASC, bsc.confidence DESC, b.title ASC
		LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []ShelfCandidateRow{}
	for rows.Next() {
		var row ShelfCandidateRow
		var isbn string
		if err := rows.Scan(
			&row.BookID, &row.Title, &isbn, &row.ShelfID, &row.Confidence,
			&row.Observations, &row.UpdatedAt, &row.Thumbnail,
		); err != nil {
			return nil, err
		}
		if row.Thumbnail == nil || *row.Thumbnail == "" {
			row.Thumbnail = googleBooksThumbnail(isbn)
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

func googleBooksThumbnail(isbn string) *string {
	isbn = strings.Map(func(r rune) rune {
		if r >= '0' && r <= '9' || r == 'X' || r == 'x' {
			return r
		}
		return -1
	}, isbn)
	if isbn == "" {
		return nil
	}
	thumbnail := "https://books.google.com/books/content?vid=ISBN" + url.QueryEscape(isbn) + "&printsec=frontcover&img=1&zoom=1&source=gbs_api"
	return &thumbnail
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
		if b.Thumbnail == nil || *b.Thumbnail == "" {
			b.Thumbnail = googleBooksThumbnail(b.ISBN)
		}
		books = append(books, b)
	}
	return books, rows.Err()
}
