package main

import (
	"context"
	"github.com/aws/aws-lambda-go/events"
	"strconv"
	"strings"
)

func relatedBooks(ctx context.Context, req events.APIGatewayV2HTTPRequest) (events.APIGatewayV2HTTPResponse, error) {
	raw := strings.TrimSuffix(strings.TrimPrefix(req.RawPath, "/api/books/"), "/related")
	id, err := strconv.Atoi(raw)
	if err != nil || id <= 0 {
		return errJSON(400, "invalid id"), nil
	}
	if bookStore == nil {
		return errJSON(503, "library DB not available"), nil
	}
	book, err := bookStore.GetByID(id)
	if err != nil {
		return errJSON(500, "library unavailable"), nil
	}
	if book == nil {
		return errJSON(404, "not found"), nil
	}
	result, err := bookStore.RelatedBooks(id, 6)
	if err != nil {
		return errJSON(500, "recommendations unavailable"), nil
	}
	return okJSON(200, map[string]any{"books": result, "strategy": "bm25-keyword-v1"}), nil
}
