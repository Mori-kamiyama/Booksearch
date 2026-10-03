package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"sort"
	"strings"
	"sync"
	"unicode"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/bedrockruntime"
	"golang.org/x/text/unicode/norm"
)

const SemanticModel = "cohere.embed-multilingual-v3"

type QueryEmbedder interface {
	Embed(context.Context, string) ([]float64, error)
}

type SemanticIndex struct {
	Version       int              `json:"version"`
	Model         string           `json:"model"`
	CatalogSHA256 string           `json:"catalog_sha256"`
	Books         []SemanticVector `json:"books"`
	Embedder      QueryEmbedder    `json:"-"`
	gate          chan struct{}
}
type SemanticVector struct {
	ID     int       `json:"id"`
	Vector []float64 `json:"vector"`
}

// Load before opening the SQLite snapshot, which may enable WAL journaling.
// An index from a different catalog is disabled rather than mixing book IDs.
func LoadSemanticIndex(path, catalog string) (*SemanticIndex, error) {
	if _, err := os.Stat(catalog + "-wal"); err == nil {
		return nil, fmt.Errorf("semantic search requires a closed catalog snapshot without WAL")
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var index SemanticIndex
	if err = json.Unmarshal(raw, &index); err != nil {
		return nil, err
	}
	if index.Version != 1 || index.Model != SemanticModel || len(index.Books) == 0 {
		return nil, fmt.Errorf("unsupported or empty semantic index")
	}
	file, err := os.Open(catalog)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	hash := sha256.New()
	if _, err = io.Copy(hash, file); err != nil {
		return nil, err
	}
	if fmt.Sprintf("%x", hash.Sum(nil)) != index.CatalogSHA256 {
		return nil, fmt.Errorf("semantic index does not match catalog snapshot")
	}
	seen := map[int]bool{}
	for i := range index.Books {
		b := &index.Books[i]
		if b.ID <= 0 || seen[b.ID] || len(b.Vector) != 1024 {
			return nil, fmt.Errorf("invalid semantic index row")
		}
		seen[b.ID] = true
		if err = normalizeVector(b.Vector); err != nil {
			return nil, err
		}
	}
	index.gate = make(chan struct{}, 1)
	return &index, nil
}

func SemanticQueryAllowed(query string, exact bool) bool {
	if exact {
		return false
	}
	q := strings.TrimSpace(norm.NFKC.String(query))
	if len([]rune(q)) == 0 || len([]rune(q)) > 200 {
		return false
	}
	isbn := strings.TrimSpace(strings.TrimPrefix(strings.TrimPrefix(strings.ToLower(q), "isbn"), ":"))
	isbn = strings.NewReplacer("-", "", " ", "").Replace(isbn)
	onlyISBN := len(isbn) == 10 || len(isbn) == 13
	for _, r := range isbn {
		if !unicode.IsDigit(r) && r != 'x' {
			onlyISBN = false
		}
	}
	if onlyISBN {
		return false
	}
	for _, r := range q {
		if unicode.IsLetter(r) || unicode.IsNumber(r) {
			return true
		}
	}
	return false
}

func normalizeVector(vector []float64) error {
	length := 0.0
	for _, value := range vector {
		if math.IsNaN(value) || math.IsInf(value, 0) {
			return fmt.Errorf("invalid embedding")
		}
		length += value * value
	}
	if length == 0 || math.IsInf(length, 0) {
		return fmt.Errorf("invalid embedding norm")
	}
	length = math.Sqrt(length)
	for i := range vector {
		vector[i] /= length
	}
	return nil
}

// The same hard-filter builder as keyword search runs before embedding/ranking.
func (s *BookStore) SearchSemantic(ctx context.Context, index *SemanticIndex, query string, filters SearchFilters, exclude map[int]bool) ([]Book, error) {
	where, args, err := s.filterWhere(filters)
	if err != nil {
		return nil, err
	}
	rows, err := s.db.QueryContext(ctx, "SELECT b.id FROM books b WHERE "+where, args...)
	if err != nil {
		return nil, err
	}
	eligible := map[int]bool{}
	for rows.Next() {
		var id int
		if err = rows.Scan(&id); err != nil {
			rows.Close()
			return nil, err
		}
		if !exclude[id] {
			eligible[id] = true
		}
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	candidates := []SemanticVector{}
	for _, b := range index.Books {
		if eligible[b.ID] {
			candidates = append(candidates, b)
		}
	}
	if len(candidates) == 0 {
		return []Book{}, nil
	}
	select {
	case index.gate <- struct{}{}:
		defer func() { <-index.gate }()
	default:
		return nil, fmt.Errorf("semantic search busy")
	}
	vector, err := index.Embedder.Embed(ctx, query)
	if err != nil {
		return nil, err
	}
	if len(vector) != len(candidates[0].Vector) {
		return nil, fmt.Errorf("embedding dimensions do not match")
	}
	if err = normalizeVector(vector); err != nil {
		return nil, err
	}
	type score struct {
		id         int
		similarity float64
	}
	scores := make([]score, 0, len(candidates))
	for _, b := range candidates {
		similarity := 0.0
		for i, value := range b.Vector {
			similarity += value * vector[i]
		}
		scores = append(scores, score{b.ID, similarity})
	}
	sort.Slice(scores, func(i, j int) bool {
		if scores[i].similarity == scores[j].similarity {
			return scores[i].id < scores[j].id
		}
		return scores[i].similarity > scores[j].similarity
	})
	books := []Book{}
	for _, score := range scores[:min(5, len(scores))] {
		if err = ctx.Err(); err != nil {
			return nil, err
		}
		book, err := s.GetByID(score.id)
		if err != nil {
			return nil, err
		}
		if book != nil {
			books = append(books, *book)
		}
	}
	return books, nil
}

type BedrockEmbedder struct {
	Client *bedrockruntime.Client
	mu     sync.Mutex
	cache  map[string][]float64
}

func (p *BedrockEmbedder) Embed(ctx context.Context, query string) ([]float64, error) {
	p.mu.Lock()
	cached, ok := p.cache[query]
	p.mu.Unlock()
	if ok {
		return append([]float64{}, cached...), nil
	}
	body, _ := json.Marshal(map[string]any{"texts": []string{query}, "input_type": "search_query", "truncate": "END"})
	response, err := p.Client.InvokeModel(ctx, &bedrockruntime.InvokeModelInput{
		ModelId: aws.String(SemanticModel), ContentType: aws.String("application/json"),
		Accept: aws.String("application/json"), Body: body,
	})
	if err != nil {
		return nil, err
	}
	var payload struct {
		Embeddings [][]float64 `json:"embeddings"`
	}
	if err = json.Unmarshal(response.Body, &payload); err != nil {
		return nil, err
	}
	if len(payload.Embeddings) != 1 || len(payload.Embeddings[0]) != 1024 {
		return nil, fmt.Errorf("invalid embedding response")
	}
	vector := payload.Embeddings[0]
	if err = normalizeVector(vector); err != nil {
		return nil, err
	}
	p.mu.Lock()
	if len(p.cache) >= 128 {
		p.cache = nil
	}
	if p.cache == nil {
		p.cache = map[string][]float64{}
	}
	p.cache[query] = append([]float64{}, vector...)
	p.mu.Unlock()
	return vector, nil
}
