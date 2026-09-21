package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"hash/fnv"
	"io"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/smithy-go"
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

type featuredManifest struct {
	Week  string `json:"week"`
	Books []Book `json:"books"`
}

const (
	featuredMaxBooks           = 20
	featuredPersistenceTimeout = 1500 * time.Millisecond
	featuredPersistenceBackoff = time.Second
)

type featuredPersistence interface {
	loadWeek(context.Context, string) (featuredManifest, error)
	loadLatest(context.Context) (featuredManifest, error)
	save(context.Context, featuredManifest) (featuredManifest, error)
}

type s3FeaturedPersistence struct {
	client *s3.Client
	bucket string
}

type BookStore struct {
	db                *sql.DB
	featuredMu        sync.Mutex
	featuredWeek      string
	featuredBooks     []Book
	featuredLastWeek  string
	featuredLastBooks []Book
	featuredPersist   featuredPersistence
	featuredRetryWeek string
	featuredRetryAt   time.Time
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
	store := &BookStore{db: d}
	if s3Client != nil && bucket != "" {
		store.featuredPersist = &s3FeaturedPersistence{client: s3Client, bucket: bucket}
	}
	return store, nil
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
	defer s.featuredMu.Unlock()
	if s.featuredWeek == weekKey {
		books := cloneBooks(s.featuredBooks)
		if len(books) > limit {
			books = books[:limit]
		}
		return books, nil
	}
	persistenceBackoff := s.featuredRetryWeek == weekKey && time.Now().Before(s.featuredRetryAt)
	var persistCtx context.Context
	var persistCancel context.CancelFunc
	if s.featuredPersist != nil && !persistenceBackoff {
		persistCtx, persistCancel = context.WithTimeout(context.Background(), featuredPersistenceTimeout)
		defer persistCancel()
	}
	if s.featuredPersist != nil && !persistenceBackoff {
		if manifest, err := s.featuredPersist.loadWeek(persistCtx, weekKey); err == nil && manifest.Week == weekKey {
			if fresh, freshErr := s.filterFeaturedBooks(manifest.Books); freshErr == nil {
				manifest.Books = fresh
			}
			if len(manifest.Books) == 0 {
				// The persisted snapshot only refers to deleted books. Rebuild
				// from the current database instead of serving stale IDs.
			} else {
				s.setFeaturedSuccess(manifest)
				return truncateFeatured(manifest.Books, limit), nil
			}
		}
		if manifest, err := s.featuredPersist.loadLatest(persistCtx); err == nil && manifest.Week != "" {
			if fresh, freshErr := s.filterFeaturedBooks(manifest.Books); freshErr == nil {
				manifest.Books = fresh
			}
			if len(manifest.Books) > 0 {
				s.setFeaturedLast(manifest)
				if manifest.Week == weekKey {
					s.featuredWeek = weekKey
					s.featuredBooks = cloneBooks(manifest.Books)
					return truncateFeatured(manifest.Books, limit), nil
				}
			}
		}
	}
	previousWeek, previousBooks := s.featuredLastWeek, cloneBooks(s.featuredLastBooks)
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
		if previousWeek != "" {
			return truncateFeatured(previousBooks, limit), nil
		}
		return nil, err
	}
	defer rows.Close()
	books, err := scanBooks(rows)
	if err != nil {
		if previousWeek != "" {
			return truncateFeatured(previousBooks, limit), nil
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
			return truncateFeatured(previousBooks, limit), nil
		}
		return []Book{}, nil
	}
	manifest := featuredManifest{Week: weekKey, Books: cloneBooks(books)}
	if s.featuredPersist != nil && !persistenceBackoff {
		persisted, persistErr := s.featuredPersist.save(persistCtx, manifest)
		if persistErr != nil {
			s.featuredRetryWeek = weekKey
			s.featuredRetryAt = time.Now().Add(featuredPersistenceBackoff)
			if previousWeek != "" {
				return truncateFeatured(previousBooks, limit), nil
			}
			// Do not mark this week complete in memory: a later request should
			// retry persistence after a transient S3 failure.
			return truncateFeatured(manifest.Books, limit), nil
		} else {
			manifest = persisted
			if fresh, freshErr := s.filterFeaturedBooks(manifest.Books); freshErr == nil {
				manifest.Books = fresh
				if len(manifest.Books) == 0 {
					// An immutable winner may now contain only deleted IDs. Do
					// not expose that stale snapshot or replace it mid-week.
					return []Book{}, nil
				}
			}
			s.featuredRetryWeek = ""
			s.featuredRetryAt = time.Time{}
		}
	}
	if persistenceBackoff {
		return truncateFeatured(manifest.Books, limit), nil
	}
	s.setFeaturedSuccess(manifest)
	return truncateFeatured(manifest.Books, limit), nil
}

// RefreshFeatured is used by the scheduled precomputation trigger. It only
// succeeds when this invocation produced or loaded a non-empty persisted
// snapshot for the current UTC week.
func (s *BookStore) RefreshFeatured() error {
	if s.featuredPersist == nil {
		return errors.New("featured persistence unavailable")
	}
	weekKey := featuredWeekKey()
	if _, err := s.Featured(featuredMaxBooks); err != nil {
		return err
	}
	s.featuredMu.Lock()
	defer s.featuredMu.Unlock()
	if s.featuredWeek != weekKey || len(s.featuredBooks) == 0 {
		return fmt.Errorf("featured week %s was not persisted", weekKey)
	}
	return nil
}

func (s *BookStore) setFeaturedLast(manifest featuredManifest) {
	s.featuredLastWeek = manifest.Week
	s.featuredLastBooks = cloneBooks(manifest.Books)
}

func (s *BookStore) setFeaturedSuccess(manifest featuredManifest) {
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
	clean := make([]Book, 0, minInt(len(books), featuredMaxBooks))
	for _, book := range books {
		if book.ID <= 0 {
			continue
		}
		if _, ok := seen[book.ID]; ok {
			continue
		}
		seen[book.ID] = struct{}{}
		clean = append(clean, book)
		if len(clean) == featuredMaxBooks {
			break
		}
	}
	return clean
}

func validateFeaturedManifest(manifest featuredManifest) error {
	if manifest.Week == "" || len(manifest.Books) == 0 || len(manifest.Books) > featuredMaxBooks {
		return errors.New("invalid featured manifest book count")
	}
	clean := sanitizeFeaturedBooks(manifest.Books)
	if len(clean) != len(manifest.Books) {
		return errors.New("invalid featured manifest book IDs")
	}
	return nil
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func (s *BookStore) filterFeaturedBooks(books []Book) ([]Book, error) {
	books = sanitizeFeaturedBooks(books)
	if len(books) == 0 {
		return books, nil
	}
	placeholders := strings.TrimSuffix(strings.Repeat("?,", len(books)), ",")
	args := make([]any, len(books))
	for i, book := range books {
		args[i] = book.ID
	}
	rows, err := s.db.Query("SELECT id FROM books WHERE id IN ("+placeholders+")", args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	existing := make(map[int]struct{}, len(books))
	for rows.Next() {
		var id int
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		existing[id] = struct{}{}
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	filtered := make([]Book, 0, len(existing))
	for _, book := range books {
		if _, ok := existing[book.ID]; ok {
			filtered = append(filtered, book)
		}
	}
	return filtered, nil
}

const (
	featuredManifestPrefix = "featured/weeks/"
	featuredLatestKey      = "featured/latest.json"
)

type featuredPointer struct {
	Week string `json:"week"`
	Key  string `json:"key"`
}

func (p *s3FeaturedPersistence) loadWeek(ctx context.Context, week string) (featuredManifest, error) {
	return p.getManifest(ctx, featuredManifestPrefix+week+".json")
}

func (p *s3FeaturedPersistence) loadLatest(ctx context.Context) (featuredManifest, error) {
	output, err := p.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(p.bucket), Key: aws.String(featuredLatestKey),
	})
	if err != nil {
		return featuredManifest{}, err
	}
	defer output.Body.Close()
	data, err := io.ReadAll(output.Body)
	if err != nil {
		return featuredManifest{}, err
	}
	var pointer featuredPointer
	if err := json.Unmarshal(data, &pointer); err != nil || pointer.Key == "" {
		if err == nil {
			err = errors.New("featured latest pointer has no key")
		}
		return featuredManifest{}, err
	}
	return p.getManifest(ctx, pointer.Key)
}

func (p *s3FeaturedPersistence) getManifest(ctx context.Context, key string) (featuredManifest, error) {
	output, err := p.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(p.bucket), Key: aws.String(key),
	})
	if err != nil {
		return featuredManifest{}, err
	}
	defer output.Body.Close()
	data, err := io.ReadAll(output.Body)
	if err != nil {
		return featuredManifest{}, err
	}
	var manifest featuredManifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return featuredManifest{}, err
	}
	if err := validateFeaturedManifest(manifest); err != nil {
		return featuredManifest{}, err
	}
	return manifest, nil
}

func (p *s3FeaturedPersistence) save(ctx context.Context, manifest featuredManifest) (featuredManifest, error) {
	if err := validateFeaturedManifest(manifest); err != nil {
		return featuredManifest{}, err
	}
	data, err := json.Marshal(manifest)
	if err != nil {
		return featuredManifest{}, err
	}
	key := featuredManifestPrefix + manifest.Week + ".json"
	_, err = p.client.PutObject(ctx, &s3.PutObjectInput{
		Bucket: aws.String(p.bucket), Key: aws.String(key), Body: bytes.NewReader(data),
		ContentType: aws.String("application/json"), IfNoneMatch: aws.String("*"),
	})
	if err != nil {
		if !isS3PreconditionFailed(err) {
			return featuredManifest{}, err
		}
		// Another Lambda won immutable manifest creation. Use its snapshot.
		manifest, err = p.loadWeek(ctx, manifest.Week)
		if err != nil {
			return featuredManifest{}, err
		}
	}
	return p.saveLatest(ctx, manifest)
}

func (p *s3FeaturedPersistence) saveLatest(ctx context.Context, manifest featuredManifest) (featuredManifest, error) {
	data, err := json.Marshal(featuredPointer{
		Week: manifest.Week, Key: featuredManifestPrefix + manifest.Week + ".json",
	})
	if err != nil {
		return featuredManifest{}, err
	}
	for attempt := 0; attempt < 3; attempt++ {
		current, etag, getErr := p.getLatestPointer(ctx)
		if getErr == nil {
			if current.Week == manifest.Week || weekRank(current.Week) > weekRank(manifest.Week) {
				winner, err := p.getManifest(ctx, current.Key)
				return winner, err
			}
		} else if !isS3NotFound(getErr) {
			return featuredManifest{}, getErr
		}
		input := &s3.PutObjectInput{
			Bucket: aws.String(p.bucket), Key: aws.String(featuredLatestKey), Body: bytes.NewReader(data),
			ContentType: aws.String("application/json"),
		}
		if etag != "" {
			input.IfMatch = aws.String(etag)
		} else {
			input.IfNoneMatch = aws.String("*")
		}
		if _, err := p.client.PutObject(ctx, input); err == nil {
			return manifest, nil
		} else if !isS3PreconditionFailed(err) {
			return featuredManifest{}, err
		}
	}
	return featuredManifest{}, errors.New("featured latest pointer update lost concurrent race")
}

func (p *s3FeaturedPersistence) getLatestPointer(ctx context.Context) (featuredPointer, string, error) {
	output, err := p.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(p.bucket), Key: aws.String(featuredLatestKey),
	})
	if err != nil {
		return featuredPointer{}, "", err
	}
	defer output.Body.Close()
	data, err := io.ReadAll(output.Body)
	if err != nil {
		return featuredPointer{}, "", err
	}
	var pointer featuredPointer
	if err := json.Unmarshal(data, &pointer); err != nil {
		return featuredPointer{}, "", err
	}
	etag := ""
	if output.ETag != nil {
		// Preserve the quoted HTTP ETag for If-Match.
		etag = *output.ETag
	}
	return pointer, etag, nil
}

func isS3PreconditionFailed(err error) bool {
	var apiErr smithy.APIError
	if errors.As(err, &apiErr) && apiErr.ErrorCode() == "PreconditionFailed" {
		return true
	}
	return strings.Contains(err.Error(), "PreconditionFailed") || strings.Contains(err.Error(), "status code: 412")
}

func isS3NotFound(err error) bool {
	var apiErr smithy.APIError
	if errors.As(err, &apiErr) {
		if apiErr.ErrorCode() == "NoSuchKey" || apiErr.ErrorCode() == "NotFound" {
			return true
		}
	}
	return strings.Contains(err.Error(), "NoSuchKey") || strings.Contains(err.Error(), "status code: 404")
}

func weekRank(value string) int {
	parts := strings.Split(value, "-")
	if len(parts) != 2 {
		return 0
	}
	var year, week int
	if _, err := fmt.Sscanf(value, "%d-%d", &year, &week); err != nil {
		return 0
	}
	return year*53 + week
}

var featuredNow = time.Now

func featuredWeekKey() string {
	year, week := featuredNow().UTC().ISOWeek()
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
