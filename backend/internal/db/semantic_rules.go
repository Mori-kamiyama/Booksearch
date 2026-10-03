package db

import (
	"math"
	"regexp"
	"sort"
	"strings"

	"github.com/ikawaha/kagome-dict/ipa"
	"github.com/ikawaha/kagome/v2/tokenizer"
	"golang.org/x/text/unicode/norm"
)

// These are conservative starting points for this model/catalog, not probabilities.
const semanticMinimumCosine = 0.50

// Weak vector matches need some textual evidence; stronger matches may use
// different wording. Neither threshold is a calibrated relevance probability.
const semanticUnanchoredMinimumCosine = 0.60
const semanticCandidateLimit = 50

var semanticTokenizer = func() *tokenizer.Tokenizer {
	t, err := tokenizer.New(ipa.Dict(), tokenizer.OmitBosEos())
	if err != nil {
		panic(err)
	}
	return t
}()

// Preserve language names: normal keyword normalization strips '+' and '#'.
var semanticLatinWord = regexp.MustCompile(`[a-z][a-z0-9]*(?:[+#]+)?`)
var semanticLLMWord = regexp.MustCompile(`(?i)\bllms?\b`)

var semanticAliases = [][]string{
	{"プログラミング", "ぷろぐらみんぐ", "programming"},
	{"llm", "llms", "large language model", "大規模言語モデル", "言語モデル", "プロンプトエンジニアリング"},
	{"データクレンジング", "データクリーニング", "データ前処理"},
	{"リクルーティング", "採用", "人材募集"},
	{"英文読解", "英文解釈", "英語リーディング"},
	{"ユーザビリティ", "使いやすさ", "使いやすい", "使いやす"},
	{"ui", "ユーザーインターフェース", "ユーザインターフェース", "ユーザーインタフェース", "ユーザインタフェース", "インターフェース", "インタフェース", "画面"},
}

var semanticGenericWords = map[string]bool{
	"本": true, "書籍": true, "こと": true, "もの": true, "ため": true,
	"方法": true, "手順": true, "入門": true, "基礎": true, "初心者": true,
	"初級": true, "上級": true, "実践": true, "おすすめ": true, "方": true,
}

type semanticTerm struct {
	variants []string
	required bool
}

func semanticText(text string) string {
	return strings.Map(func(r rune) rune {
		if r >= 'ァ' && r <= 'ヶ' {
			return r - 0x60
		}
		return r
	}, strings.ToLower(norm.NFKC.String(text)))
}

func semanticLiteralContains(text, term string) bool {
	if semanticLatinWord.MatchString(term) && semanticLatinWord.FindString(term) == term {
		for _, word := range semanticLatinWord.FindAllString(text, -1) {
			if word == term {
				return true
			}
		}
		return false
	}
	return strings.Contains(text, term)
}

// Resolve common spelling/abbreviation ambiguity before embedding. An isolated
// LLM otherwise retrieves Linux/Scrum books with this multilingual model.
func semanticEmbeddingQuery(query string) string {
	text := norm.NFKC.String(query)
	text = strings.ReplaceAll(text, "ぷろぐらみんぐ", "プログラミング")
	return semanticLLMWord.ReplaceAllString(text, "大規模言語モデル LLM")
}

func semanticQueryTerms(query string) []semanticTerm {
	// Keep the original kana for Japanese morphological analysis; fold kana only
	// when comparing extracted terms against a book, not before tokenization.
	text := strings.ToLower(norm.NFKC.String(query))
	terms := []semanticTerm{}
	seen := map[string]bool{}
	add := func(variants []string, required bool) {
		normalized := make([]string, len(variants))
		for i, variant := range variants {
			normalized[i] = semanticText(variant)
		}
		variants = normalized
		if !seen[variants[0]] {
			seen[variants[0]] = true
			terms = append(terms, semanticTerm{variants, required})
		}
	}
	// Recognize whole compounds before morphological splitting (e.g. 離乳食
	// must never be accepted just because a nutrition book contains 食).
	for _, variants := range semanticAliases {
		matched := false
		required := false
		for _, variant := range variants {
			if semanticLiteralContains(text, variant) {
				matched = true
				// Explicit Latin identifiers (LLM/UI/etc.) remain constraints;
				// natural-language concepts are allowed to match semantically.
				required = required || semanticLatinWord.FindString(variant) == variant
			}
		}
		if matched {
			add(variants, required)
			for _, variant := range variants {
				// Latin words are removed separately with token boundaries below.
				if variant != "ui" {
					text = strings.ReplaceAll(text, variant, " ")
				}
			}
		}
	}
	text = semanticLatinWord.ReplaceAllStringFunc(text, func(word string) string {
		if word != "ui" || !seen["ui"] {
			add([]string{word}, true)
		}
		return " "
	})
	var compound strings.Builder
	flush := func() {
		word := compound.String()
		if len([]rune(word)) >= 2 {
			add([]string{word}, false)
		}
		compound.Reset()
	}
	for _, token := range semanticTokenizer.Tokenize(text) {
		pos, _ := token.FeatureAt(0)
		if pos == "名詞" && !semanticGenericWords[token.Surface] && hasSearchContent(token.Surface) {
			compound.WriteString(token.Surface)
		} else {
			flush()
		}
	}
	flush()
	return terms
}

type semanticRankedBook struct {
	Book       Book    `json:"book"`
	Cosine     float64 `json:"cosine"`
	Score      float64 `json:"score"`
	Coverage   float64 `json:"coverage"`
}

func semanticCoverage(terms []semanticTerm, text string) (float64, bool) {
	matches := 0
	anchorsOK := true
	for _, term := range terms {
		found := false
		for _, variant := range term.variants {
			if semanticLiteralContains(text, variant) {
				found = true
				break
			}
		}
		if found {
			matches++
		} else if term.required {
			anchorsOK = false
		}
	}
	if len(terms) == 0 {
		return 0, false
	}
	return float64(matches) / float64(len(terms)), anchorsOK
}

// Rules only triage candidates; surviving books keep their vector order.
// Only verified metadata returned by GetByID is used, never generated guesses.
func rankSemanticBooks(query string, candidates []semanticRankedBook) []semanticRankedBook {
	terms := semanticQueryTerms(query)
	result := []semanticRankedBook{}
	if len(terms) == 0 {
		return result
	}
	for _, candidate := range candidates {
		if math.IsNaN(candidate.Cosine) || math.IsInf(candidate.Cosine, 0) || candidate.Cosine < semanticMinimumCosine {
			continue
		}
		title := semanticText(candidate.Book.Title)
		text := title + "\n" + semanticText(candidate.Book.Authors)
		if candidate.Book.Description != nil {
			text += "\n" + semanticText(*candidate.Book.Description)
		}
		coverage, anchorsOK := semanticCoverage(terms, text)
		if !anchorsOK || (coverage == 0 && candidate.Cosine < semanticUnanchoredMinimumCosine) {
			continue
		}
		candidate.Coverage = coverage
		candidate.Score = candidate.Cosine
		result = append(result, candidate)
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].Score == result[j].Score {
			return result[i].Book.ID < result[j].Book.ID
		}
		return result[i].Score > result[j].Score
	})
	return result[:min(5, len(result))]
}
