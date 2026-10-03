package db

import (
	"database/sql"
	"testing"
)

func TestSearchAcronymDoesNotMatchInsideEnglishNames(t *testing.T) {
	raw, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer raw.Close()
	if err = CreateSchema(raw); err != nil {
		t.Fatal(err)
	}
	for _, statement := range []string{
		`INSERT INTO books(id,title,authors,title_norm,authors_norm) VALUES(1,'Billmeyer and Saltzman','Berns','billmeyerandsaltzman','berns'),(2,'教養としてのAI講義','Mitchell, Melanie','教養としてのai講義','mitchellmelanie'),(3,'LLMの実践','著者','llmの実践','著者'),(4,'Learning with LLM','著者','learningwithllm','著者')`,
		`CREATE TABLE book_search_terms(book_id INTEGER,value_norm TEXT)`,
		`INSERT INTO book_search_terms VALUES(1,'billmeyerandsaltzman'),(2,'mitchellmelanie'),(3,'llmの実践'),(4,'learningwithllm')`,
	} {
		if _, err = raw.Exec(statement); err != nil {
			t.Fatal(err)
		}
	}
	store := &Store{db: raw}
	for _, query := range []string{"LLM", "llm", "ＬＬＭ"} {
		result, err := store.SearchFiltered(query, 10, 0, SearchFilters{})
		if err != nil || result.Total != 2 {
			t.Fatalf("%s: %+v %v", query, result, err)
		}
		for _, book := range result.Books {
			if book.ID < 3 {
				t.Fatalf("substring leakage: %+v", book)
			}
		}
	}
	if _, err = raw.Exec(`INSERT INTO book_search_terms VALUES(2,'llm')`); err != nil {
		t.Fatal(err)
	}
	result, err := store.SearchFiltered("LLM", 10, 0, SearchFilters{})
	if err != nil || result.Total != 3 {
		t.Fatalf("explicit alias lost: %+v %v", result, err)
	}
}
