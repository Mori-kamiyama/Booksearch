package handler

import (
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"time"

	"booksearch/backend/internal/db"
	"booksearch/backend/internal/job"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

type Handler struct {
	Store   *db.Store
	Jobs    *job.Manager
	JobsDir string
	TagMap  string
}

func (h *Handler) SearchBooks(c *gin.Context) {
	q := c.Query("q")
	if q == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "q is required"})
		return
	}
	limit := 20
	if l, err := strconv.Atoi(c.Query("limit")); err == nil && l > 0 {
		limit = l
	}
	books, err := h.Store.Search(q, limit)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	if books == nil {
		books = []db.Book{}
	}
	c.JSON(http.StatusOK, gin.H{"books": books, "query": q})
}

func (h *Handler) GetBook(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid id"})
		return
	}
	book, err := h.Store.GetByID(id)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	if book == nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "not found"})
		return
	}
	c.JSON(http.StatusOK, book)
}

func (h *Handler) Scan(c *gin.Context) {
	file, header, err := c.Request.FormFile("image")
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "image field required"})
		return
	}
	defer file.Close()

	id := uuid.New().String()
	if err := h.Jobs.Create(id); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	// アップロードファイルを jobs/<id>/ に保存
	ext := filepath.Ext(header.Filename)
	uploadPath := filepath.Join(h.JobsDir, id, "upload"+ext)
	dst, err := os.Create(uploadPath)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	if _, err = io.Copy(dst, file); err != nil {
		dst.Close()
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	dst.Close()

	h.Jobs.Start(id, uploadPath)
	c.JSON(http.StatusAccepted, gin.H{"job_id": id})
}

func (h *Handler) GetJob(c *gin.Context) {
	id := c.Param("id")
	state, err := h.Jobs.GetState(id)
	if err != nil || state == nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "job not found"})
		return
	}

	resp := gin.H{
		"job_id":  state.ID,
		"status":  state.Status,
		"created": state.CreatedAt.Format(time.RFC3339),
	}
	if state.Error != "" {
		resp["error"] = state.Error
	}
	if state.Status == job.StatusDone {
		catalog, _ := h.Jobs.GetCatalog(id)
		resp["catalog"] = catalog
	}
	c.JSON(http.StatusOK, resp)
}

func (h *Handler) GetShelves(c *gin.Context) {
	if h.TagMap == "" {
		c.JSON(http.StatusOK, gin.H{"shelves": []any{}})
		return
	}
	data, err := os.ReadFile(h.TagMap)
	if err != nil {
		c.JSON(http.StatusOK, gin.H{"shelves": []any{}})
		return
	}
	// マッピングJSONをそのまま返す（フロントで使いやすい形に）
	c.Data(http.StatusOK, "application/json", data)
}

func (h *Handler) ServeStatic(c *gin.Context) {
	// outputs/ 配下のファイルを安全に配信
	rel := c.Param("path")
	abs := filepath.Join(h.JobsDir, "..", rel)
	clean := filepath.Clean(abs)
	allowed := filepath.Clean(filepath.Join(h.JobsDir, ".."))
	if len(clean) < len(allowed) || clean[:len(allowed)] != allowed {
		c.Status(http.StatusForbidden)
		return
	}
	if _, err := os.Stat(clean); os.IsNotExist(err) {
		c.Status(http.StatusNotFound)
		return
	}
	c.Header("Cache-Control", "max-age=3600")
	c.File(clean)
}

// Health check
func (h *Handler) Health(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"status": "ok", "time": fmt.Sprintf("%d", time.Now().Unix())})
}
