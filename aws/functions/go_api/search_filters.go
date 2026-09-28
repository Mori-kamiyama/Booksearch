package main

import (
	"fmt"
	"net/url"
	"strconv"
	"strings"
)

type SearchFilters struct {
	Author   string
	Topic    string
	Genre    string
	MinPages int
	MaxPages int
	Level    string
}

func ParseSearchFilters(values url.Values) (SearchFilters, error) {
	f := SearchFilters{Author: strings.TrimSpace(values.Get("author")), Topic: values.Get("topic"), Genre: values.Get("genre"), Level: values.Get("level")}
	for key, dest := range map[string]*int{"min_pages": &f.MinPages, "max_pages": &f.MaxPages} {
		if value := values.Get(key); value != "" {
			number, err := strconv.Atoi(value)
			if err != nil || number < 1 || number > 100000 {
				return f, fmt.Errorf("invalid %s", key)
			}
			*dest = number
		}
	}
	if f.MaxPages > 0 && f.MinPages > f.MaxPages {
		return f, fmt.Errorf("invalid page range")
	}
	if f.Level != "" && f.Level != "beginner" && f.Level != "intermediate" && f.Level != "advanced" {
		return f, fmt.Errorf("invalid level")
	}
	return f, nil
}
func (f SearchFilters) Empty() bool { return f == (SearchFilters{}) }
func (s *BookStore) filterWhere(f SearchFilters) (string, []any, error) {
	clauses, args := []string{}, []any{}
	if f.Author != "" {
		clauses = append(clauses, "b.authors_norm = ?")
		args = append(args, normalizeQuery(f.Author))
	}
	if f.Genre != "" || f.Topic != "" || f.MinPages > 0 || f.MaxPages > 0 || f.Level != "" {
		var count int
		if err := s.db.QueryRow("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('book_discovery','book_topics')").Scan(&count); err != nil {
			return "", nil, err
		}
		if count != 2 {
			return "", nil, fmt.Errorf("search discovery index is unavailable")
		}
	}
	if f.Genre != "" {
		clauses = append(clauses, "EXISTS (SELECT 1 FROM book_topics g WHERE g.book_id=b.id AND g.topic_id=?)")
		args = append(args, f.Genre)
	}
	if f.Topic != "" {
		clauses = append(clauses, "EXISTS (SELECT 1 FROM book_topics t WHERE t.book_id=b.id AND t.topic_id=?)")
		args = append(args, f.Topic)
	}
	if f.MinPages > 0 {
		clauses = append(clauses, "EXISTS (SELECT 1 FROM book_discovery d WHERE d.book_id=b.id AND d.page_count>=?)")
		args = append(args, f.MinPages)
	}
	if f.MaxPages > 0 {
		clauses = append(clauses, "EXISTS (SELECT 1 FROM book_discovery d WHERE d.book_id=b.id AND d.page_count<=?)")
		args = append(args, f.MaxPages)
	}
	if f.Level != "" {
		clauses = append(clauses, "EXISTS (SELECT 1 FROM book_discovery d WHERE d.book_id=b.id AND d.level=?)")
		args = append(args, f.Level)
	}
	if len(clauses) == 0 {
		return "1=1", args, nil
	}
	return strings.Join(clauses, " AND "), args, nil
}
