package main

import (
	"net/http"

	"booksearch/backend/internal/handler"

	"github.com/gin-gonic/gin"
)

func buildRouter(h *handler.Handler) *gin.Engine {
	r := gin.New()
	r.Use(gin.Recovery())
	r.Use(corsMiddleware())

	api := r.Group("/api")
	{
		api.GET("/health", h.Health)
		api.GET("/books/search", h.SearchBooks)
		api.GET("/books/:id", h.GetBook)
		api.GET("/shelf-candidates", h.ShelfCandidates)
		api.POST("/scan", h.Scan)
		api.GET("/jobs/:id", h.GetJob)
		api.GET("/shelves", h.GetShelves)
		api.POST("/tags/detect", h.DetectTags)
	}
	r.GET("/static/*path", h.ServeStatic)

	return r
}

func corsMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Header("Access-Control-Allow-Origin", "*")
		c.Header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		c.Header("Access-Control-Allow-Headers", "Content-Type")
		if c.Request.Method == http.MethodOptions {
			c.AbortWithStatus(http.StatusNoContent)
			return
		}
		c.Next()
	}
}
