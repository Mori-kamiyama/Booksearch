package db

import "database/sql"

// Missing optional tables are supported for older catalogues; query failures are not hidden.
func (s *Store) enrichBook(book *Book) error {
	for _, table := range []string{"book_discovery", "book_metadata"} {
		var exists int
		if err := s.db.QueryRow("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", table).Scan(&exists); err != nil {
			return err
		}
		if exists == 0 {
			continue
		}
		var err error
		if table == "book_discovery" {
			err = s.db.QueryRow("SELECT page_count,level FROM book_discovery WHERE book_id=?", book.ID).Scan(&book.PageCount, &book.Level)
		} else {
			err = s.db.QueryRow("SELECT description,source FROM book_metadata WHERE book_id=? AND fetch_status='matched'", book.ID).Scan(&book.Description, &book.DescriptionSource)
		}
		if err != nil && err != sql.ErrNoRows {
			return err
		}
	}
	return nil
}
