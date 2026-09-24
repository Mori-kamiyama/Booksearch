package main

import (
	"database/sql"
	"strings"

	_ "modernc.org/sqlite"
)

type Book struct {
	ID                 int     `json:"id"`
	Title              string  `json:"title"`
	Authors            string  `json:"authors"`
	Publisher          string  `json:"publisher"`
	PublishedDate      string  `json:"published_date"`
	ClassNumber        string  `json:"class_number"`
	RegistrationNumber string  `json:"registration_number"`
	ISBN               string  `json:"isbn"`
	Thumbnail          *string `json:"thumbnail"`
	InfoLink           *string `json:"info_link"`
}

type BookStore struct {
	db *sql.DB
}

func OpenBookStore(path string) (*BookStore, error) {
	d, err := sql.Open("sqlite", path+"?mode=ro&_pragma=journal_mode(off)")
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
		); err != nil {
			return nil, err
		}
		books = append(books, b)
	}
	return books, rows.Err()
}
