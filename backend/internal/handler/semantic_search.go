package handler

import (
	"context"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"

	"booksearch/backend/internal/db"
	"github.com/gin-gonic/gin"
)

func (h *Handler) SemanticStatus(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"available": h.Semantic != nil && h.Semantic.Embedder != nil})
}

func (h *Handler) SemanticBooks(c *gin.Context) {
	if h.Semantic == nil || h.Semantic.Embedder == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "semantic search unavailable"})
		return
	}
	query := strings.TrimSpace(c.Query("q"))
	if !db.SemanticQueryAllowed(query, c.Query("exact") == "1") {
		c.JSON(http.StatusBadRequest, gin.H{"error": "query cannot use semantic search"})
		return
	}
	filters, err := db.ParseSearchFilters(c.Request.URL.Query())
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	excluded := map[int]bool{}
	values := strings.Split(c.Query("exclude"), ",")
	if len(values) > 100 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "too many excluded books"})
		return
	}
	for _, value := range values {
		if value == "" {
			continue
		}
		id, err := strconv.Atoi(value)
		if err != nil || id <= 0 {
			c.JSON(http.StatusBadRequest, gin.H{"error": "invalid excluded book"})
			return
		}
		excluded[id] = true
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), 8*time.Second)
	defer cancel()
	books, err := h.Store.SearchSemantic(ctx, h.Semantic, query, filters, excluded)
	if err != nil {
		log.Printf("semantic search failed: %v", err)
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "semantic search temporarily unavailable"})
		return
	}
	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, gin.H{"books": books})
}
