package db

import (
	"database/sql"
	"fmt"
	"math"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

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
	q := "%" + strings.ToLower(strings.TrimSpace(query)) + "%"
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

func (s *Store) attachShelfCandidates(books []Book) ([]Book, error) {
	for i := range books {
		candidates, err := s.ShelfCandidates(books[i].ID, 5)
		if err != nil {
			return nil, err
		}
		books[i].ShelfCandidates = candidates
		if len(candidates) > 0 {
			books[i].ShelfIDs = make([]string, 0, len(candidates))
			for _, c := range candidates {
				books[i].ShelfIDs = append(books[i].ShelfIDs, c.ShelfID)
			}
		}
	}
	return books, nil
}

func (s *Store) ObserveShelf(bookID int, shelfID, sourceJob, sourceCrop string, evidenceScore float64) (bool, error) {
	if bookID <= 0 || strings.TrimSpace(shelfID) == "" || strings.TrimSpace(sourceJob) == "" || strings.TrimSpace(sourceCrop) == "" {
		return false, nil
	}
	if evidenceScore < 0 {
		evidenceScore = 0
	}
	if evidenceScore > 1 {
		evidenceScore = 1
	}
	now := time.Now().UTC().Format(time.RFC3339)
	res, err := s.db.Exec(`
		INSERT OR IGNORE INTO book_shelf_observations
			(book_id, shelf_id, source_job, source_crop, evidence_score, observed_at)
		VALUES (?, ?, ?, ?, ?, ?)`,
		bookID, shelfID, sourceJob, sourceCrop, evidenceScore, now,
	)
	if err != nil {
		return false, err
	}
	affected, err := res.RowsAffected()
	if err != nil {
		return false, err
	}
	if affected == 0 {
		return false, nil
	}
	return true, s.recomputeShelfCandidate(bookID, shelfID)
}

func (s *Store) recomputeShelfCandidate(bookID int, shelfID string) error {
	var observations int
	var avgScore float64
	var lastSeen string
	err := s.db.QueryRow(`
		SELECT COUNT(*), COALESCE(AVG(evidence_score), 0), COALESCE(MAX(observed_at), '')
		FROM book_shelf_observations
		WHERE book_id = ? AND shelf_id = ?`, bookID, shelfID,
	).Scan(&observations, &avgScore, &lastSeen)
	if err != nil {
		return err
	}
	confidence := avgScore * (1 - math.Pow(0.72, float64(observations)))
	if confidence > 0.99 {
		confidence = 0.99
	}
	_, err = s.db.Exec(`
		INSERT INTO book_shelf_candidates
			(book_id, shelf_id, confidence, observations, last_seen_at)
		VALUES (?, ?, ?, ?, ?)
		ON CONFLICT(book_id, shelf_id) DO UPDATE SET
			confidence = excluded.confidence,
			observations = excluded.observations,
			last_seen_at = excluded.last_seen_at`,
		bookID, shelfID, confidence, observations, lastSeen,
	)
	return err
}

func (s *Store) UpdateShelfConfidenceFromCatalog(sourceJob string, catalog map[string]any) (int, error) {
	entries, _ := catalog["entries"].([]any)
	updated := 0
	for i, rawEntry := range entries {
		entry, ok := rawEntry.(map[string]any)
		if !ok {
			continue
		}
		shelfID, _ := entry["shelf_id"].(string)
		if shelfID == "" {
			continue
		}
		sourceCrop := firstString(entry, "crop_id", "box_id", "crop_key", "crop_image")
		if sourceCrop == "" {
			sourceCrop = fmt.Sprintf("entry_%03d", i)
		}
		books, _ := entry["books"].([]any)
		for _, rawBook := range books {
			book, ok := rawBook.(map[string]any)
			if !ok {
				continue
			}
			bookID, score, ok := topLibraryCandidate(book)
			if !ok {
				continue
			}
			inserted, err := s.ObserveShelf(bookID, shelfID, sourceJob, sourceCrop, score)
			if err != nil {
				return updated, err
			}
			if inserted {
				updated++
			}
		}
	}
	return updated, nil
}

func firstString(m map[string]any, keys ...string) string {
	for _, key := range keys {
		if v, ok := m[key].(string); ok && v != "" {
			return v
		}
	}
	return ""
}

func topLibraryCandidate(book map[string]any) (int, float64, bool) {
	lookup, _ := book["book_lookup"].(map[string]any)
	candidates, _ := lookup["candidates"].([]any)
	if len(candidates) == 0 {
		return 0, 0, false
	}
	candidate, _ := candidates[0].(map[string]any)
	id, ok := numberAsInt(candidate["library_db_id"])
	if !ok || id <= 0 {
		return 0, 0, false
	}
	score, ok := numberAsFloat(candidate["score"])
	if !ok {
		score = 0.65
	}
	return id, score, true
}

func numberAsInt(v any) (int, bool) {
	switch n := v.(type) {
	case int:
		return n, true
	case int64:
		return int(n), true
	case float64:
		return int(n), true
	case jsonNumber:
		i, err := n.Int64()
		return int(i), err == nil
	default:
		return 0, false
	}
}

func numberAsFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case jsonNumber:
		f, err := n.Float64()
		return f, err == nil
	default:
		return 0, false
	}
}

type jsonNumber interface {
	Int64() (int64, error)
	Float64() (float64, error)
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
