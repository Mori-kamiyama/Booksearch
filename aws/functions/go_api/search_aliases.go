package main

import "strings"

// Known abbreviations are words, not arbitrary substrings of English names.
func searchAcronym(term string) bool {
	switch term {
	case "llm", "llms", "ai", "ui", "ux", "api", "sql", "html", "css", "nlp", "bert":
		return true
	}
	return false
}

func acronymSearchWhere(term string) (string, []any) {
	clauses, args := []string{}, []any{}
	for _, field := range []string{"b.title", "b.authors"} {
		for _, pattern := range []string{term, term + "[^a-z0-9]*", "*[^a-z0-9]" + term, "*[^a-z0-9]" + term + "[^a-z0-9]*"} {
			clauses = append(clauses, "LOWER(COALESCE("+field+",'')) GLOB ?")
			args = append(args, pattern)
		}
	}
	return "(" + strings.Join(clauses, " OR ") + ")", args
}

func normalizeReading(value string) string {
	return strings.Map(func(r rune) rune {
		if r >= 'ァ' && r <= 'ヶ' {
			return r - 0x60
		}
		return r
	}, normalizeQuery(value))
}

// Old catalogues remain searchable; new catalogues add verified readings and
// curated aliases to each AND term before filters/count/pagination are applied.
func (s *BookStore) discoverySearchWhere(terms []searchTerm) (string, []any, error) {
	var exists int
	if err := s.db.QueryRow("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='book_search_terms'").Scan(&exists); err != nil {
		return "", nil, err
	}
	if exists == 0 {
		where, args := searchWhere(terms)
		return where, args, nil
	}
	clauses, args := []string{}, []any{}
	for _, term := range terms {
		base, baseArgs := searchWhere([]searchTerm{term})
		if searchAcronym(term.normalized) {
			clauses = append(clauses, "("+base+` OR EXISTS (SELECT 1 FROM book_search_terms st WHERE st.book_id=b.id AND st.value_norm = ?))`)
			args = append(args, baseArgs...)
			args = append(args, term.normalized)
			continue
		}
		clauses = append(clauses, "("+base+` OR EXISTS (SELECT 1 FROM book_search_terms st WHERE st.book_id=b.id AND st.value_norm LIKE ? ESCAPE '\'))`)
		args = append(args, baseArgs...)
		args = append(args, "%"+escapeLike(normalizeReading(term.normalized))+"%")
	}
	return strings.Join(clauses, " AND "), args, nil
}
