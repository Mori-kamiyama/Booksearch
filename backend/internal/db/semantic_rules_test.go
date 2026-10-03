package db

import (
	"encoding/json"
	"os"
	"testing"
	"time"
)

func TestSemanticRulesRejectUnsupportedCandidates(t *testing.T) {
	description := "データクレンジングを扱うPythonのデータ前処理。"
	tests := []struct {
		query string
		book  Book
		cos   float64
		keep  bool
	}{
		{"離乳食", Book{Title: "食と栄養", Description: semanticString("食品と食事の科学")}, 0.9, false},
		{"zxqv987qqq", Book{Title: "囲碁AI入門"}, 0.9, false},
		{"C++", Book{Title: "C#プログラミング"}, 0.9, false},
		{"C++", Book{Title: "Cプログラミング"}, 0.9, false},
		{"C++", Book{Title: "C++入門"}, 0.6, true},
		{"ぷろぐらみんぐ入門", Book{Title: "プログラミング入門"}, 0.6, true},
		{"LLM", Book{Title: "BERTによる自然言語処理入門", Description: semanticString("言語モデルを学ぶ")}, 0.6, true},
		{"LLM", Book{Title: "大規模スクラムLarge-Scale Scrum"}, 0.9, false},
		{"LLM", Book{Title: "はじめてのLinux"}, 0.9, false},
		{"UI", Book{Title: "Building tools"}, 0.9, false},
		{"UI", Book{Title: "インタフェースデザイン"}, 0.6, true},
		{"使いやすいアプリの画面を設計したい", Book{Title: "アプリ入門", Description: semanticString("アプリの画面を設計する方法")}, 0.9, false},
		{"Pythonでデータを分析したい", Book{Title: "Javaによるデータ分析"}, 0.9, false},
		{"データクレンジング", Book{Title: "pandas前処理", Description: &description}, 0.55, true},
		{"データクレンジング", Book{Title: "pandas前処理", Description: &description}, 0.49, false},
		{"本のおすすめ", Book{Title: "おすすめの本"}, 0.9, false},
	}
	for _, tt := range tests {
		t.Run(tt.query+"/"+tt.book.Title, func(t *testing.T) {
			got := rankSemanticBooks(tt.query, []semanticRankedBook{{Book: tt.book, Cosine: tt.cos}})
			if (len(got) > 0) != tt.keep {
				t.Fatalf("keep=%v, result=%+v terms=%+v", tt.keep, got, semanticQueryTerms(tt.query))
			}
		})
	}
}

func semanticString(value string) *string { return &value }

func TestSemanticEmbeddingQueryResolvesKanaAndAcronyms(t *testing.T) {
	for query, want := range map[string]string{
		"ぷろぐらみんぐ入門": "プログラミング入門",
		"LLM":       "大規模言語モデル LLM",
		"llm 入門":    "大規模言語モデル LLM 入門",
		"C++":       "C++",
		"billmeyer": "billmeyer",
	} {
		if got := semanticEmbeddingQuery(query); got != want {
			t.Errorf("%q: got %q want %q", query, got, want)
		}
	}
}

func TestSemanticRulesPreferTitleEvidenceWithoutRescuingLowSimilarity(t *testing.T) {
	books := []semanticRankedBook{
		{Book: Book{ID: 1, Title: "技術の概説", Description: semanticString("データクレンジングを扱う")}, Cosine: 0.59},
		{Book: Book{ID: 2, Title: "データクレンジングの実践"}, Cosine: 0.55},
		{Book: Book{ID: 3, Title: "データクレンジングの教科書"}, Cosine: 0.49},
	}
	got := rankSemanticBooks("データクレンジング", books)
	if len(got) != 2 || got[0].Book.ID != 2 || got[1].Book.ID != 1 {
		t.Fatalf("unexpected ranking: %+v", got)
	}
}

// Opt-in catalog evaluation uses saved query embeddings, never a model API.
// The fixture contains full-catalog vector top-50 and verified book metadata.
func TestSemanticRuleCatalogEvaluation(t *testing.T) {
	path := os.Getenv("SEMANTIC_RULE_FIXTURE")
	if path == "" {
		t.Skip("set SEMANTIC_RULE_FIXTURE to evaluate the saved catalog")
	}
	var cases []struct {
		Query         string               `json:"query"`
		Candidates    []semanticRankedBook `json:"candidates"`
		ExpectedFirst string               `json:"expected_first"`
		Empty         bool                 `json:"empty"`
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	report := []map[string]any{}
	for _, c := range cases {
		terms := [][]string{}
		for _, term := range semanticQueryTerms(c.Query) {
			terms = append(terms, term.variants)
		}
		start := time.Now()
		got := rankSemanticBooks(c.Query, c.Candidates)
		report = append(report, map[string]any{"query": c.Query, "books": got, "terms": terms, "microseconds": time.Since(start).Microseconds()})
		if c.Empty && len(got) != 0 {
			t.Errorf("%s should have no results: %+v", c.Query, got)
		}
		if !c.Empty && len(got) == 0 {
			t.Errorf("%s lost all candidates, terms=%+v", c.Query, semanticQueryTerms(c.Query))
		}
		if c.ExpectedFirst != "" && (len(got) == 0 || got[0].Book.Title != c.ExpectedFirst) {
			t.Errorf("%s expected first %s, got %+v", c.Query, c.ExpectedFirst, got)
		}
	}
	if path := os.Getenv("SEMANTIC_RULE_REPORT"); path != "" {
		raw, err := json.MarshalIndent(report, "", "  ")
		if err != nil {
			t.Fatal(err)
		}
		if err = os.WriteFile(path, raw, 0600); err != nil {
			t.Fatal(err)
		}
	}
}
