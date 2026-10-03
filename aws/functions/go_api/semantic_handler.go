package main

import (
	"context"
	"log"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/aws/aws-lambda-go/events"
)

func semanticBooks(ctx context.Context, req events.APIGatewayV2HTTPRequest) (events.APIGatewayV2HTTPResponse, error) {
	if semanticIndex == nil || semanticIndex.Embedder == nil || bookStore == nil {
		return errJSON(503, "semantic search unavailable"), nil
	}
	query := strings.TrimSpace(req.QueryStringParameters["q"])
	if !SemanticQueryAllowed(query, req.QueryStringParameters["exact"] == "1") {
		return errJSON(400, "query cannot use semantic search"), nil
	}
	values := url.Values{}
	for key, value := range req.QueryStringParameters {
		values.Set(key, value)
	}
	filters, err := ParseSearchFilters(values)
	if err != nil {
		return errJSON(400, err.Error()), nil
	}
	exclude := map[int]bool{}
	ids := strings.Split(values.Get("exclude"), ",")
	if len(ids) > 100 {
		return errJSON(400, "too many excluded books"), nil
	}
	for _, value := range ids {
		if value == "" {
			continue
		}
		id, err := strconv.Atoi(value)
		if err != nil || id <= 0 {
			return errJSON(400, "invalid excluded book"), nil
		}
		exclude[id] = true
	}
	ctx, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	books, err := bookStore.SearchSemantic(ctx, semanticIndex, query, filters, exclude)
	if err != nil {
		log.Printf("semantic search failed: %v", err)
		return errJSON(503, "semantic search temporarily unavailable"), nil
	}
	attachShelfCandidates(ctx, books)
	attachCoverCache(ctx, books)
	response := okJSON(200, map[string]any{"books": books})
	response.Headers["Cache-Control"] = "no-store"
	return response, nil
}
