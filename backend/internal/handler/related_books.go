package handler

import (
	"github.com/gin-gonic/gin"
	"net/http"
	"strconv"
)

func (h *Handler) RelatedBooks(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil || id <= 0 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid id"})
		return
	}
	book, err := h.Store.GetByID(id)
	if err != nil {
		c.JSON(500, gin.H{"error": "library unavailable"})
		return
	}
	if book == nil {
		c.JSON(404, gin.H{"error": "not found"})
		return
	}
	books, err := h.Store.RelatedBooks(id, 6)
	if err != nil {
		c.JSON(500, gin.H{"error": "recommendations unavailable"})
		return
	}
	c.JSON(200, gin.H{"books": books, "strategy": "bm25-keyword-v1"})
}
