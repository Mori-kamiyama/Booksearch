package db

import (
	"database/sql"
	"encoding/json"
	"errors"
)

type RelatedBook struct {
	Book
	Reasons []string `json:"reasons"`
}

// RelatedBooks reads only the precomputed, versioned ranking. Missing metadata
// is an empty result, never a random recommendation presented as related.
func (s *Store) RelatedBooks(id, limit int) ([]RelatedBook, error) {
	result := []RelatedBook{}
	if limit <= 0 || limit > 20 {
		limit = 6
	}
	var exists int
	err := s.db.QueryRow("SELECT 1 FROM sqlite_master WHERE type='table' AND name='book_recommendations'").Scan(&exists)
	if errors.Is(err, sql.ErrNoRows) {
		return result, nil
	}
	if err != nil {
		return nil, err
	}
	strategy := "bm25-keyword-v1"
	// A completed v2 run may intentionally contain zero candidates. Do not
	// fill it with weaker v1 recommendations. Old catalogues need no migration.
	var hasRuns int
	if err := s.db.QueryRow("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='book_recommendation_runs'").Scan(&hasRuns); err != nil {
		return nil, err
	}
	if hasRuns > 0 {
		var completed int
		if err := s.db.QueryRow("SELECT COUNT(*) FROM book_recommendation_runs WHERE source_book_id=? AND strategy='content-diverse-v2'", id).Scan(&completed); err != nil {
			return nil, err
		}
		if completed > 0 {
			strategy = "content-diverse-v2"
		}
	}
	rows, err := s.db.Query(`SELECT r.recommended_book_id, r.reasons_json
 FROM book_recommendations r JOIN books b ON b.id=r.recommended_book_id
 WHERE r.source_book_id=? AND r.recommended_book_id != ? AND r.strategy=? AND r.score > 0
 ORDER BY r.score DESC, r.recommended_book_id ASC LIMIT ?`, id, id, strategy, limit)
	if err != nil {
		return nil, err
	}
	type candidate struct {
		id      int
		reasons string
	}
	candidates := []candidate{}
	for rows.Next() {
		var c candidate
		if err := rows.Scan(&c.id, &c.reasons); err != nil {
			rows.Close()
			return nil, err
		}
		candidates = append(candidates, c)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	seen := map[int]bool{}
	for _, c := range candidates {
		if c.id <= 0 || seen[c.id] {
			continue
		}
		seen[c.id] = true
		book, err := s.GetByID(c.id)
		if err != nil {
			return nil, err
		}
		if book == nil {
			continue
		}
		reasons := []string{}
		var raw []string
		if json.Unmarshal([]byte(c.reasons), &raw) == nil {
			for _, reason := range raw {
				switch reason {
				case "同じテーマの本", "同じ著者の作品", "分類が近い本", "同じ大分類の本", "同じカテゴリの本", "内容のキーワードが近い本":
					reasons = append(reasons, reason)
				}
			}
		}
		result = append(result, RelatedBook{Book: *book, Reasons: reasons})
	}
	return result, nil
}
