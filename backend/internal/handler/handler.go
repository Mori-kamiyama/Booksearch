package handler

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
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

func (h *Handler) ShelfCandidates(c *gin.Context) {
	limit := 500
	if l, err := strconv.Atoi(c.Query("limit")); err == nil && l > 0 {
		limit = l
	}
	candidates, err := h.Store.AllShelfCandidates(limit)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	h.attachCropURLs(candidates)
	c.JSON(http.StatusOK, gin.H{"candidates": candidates})
}

type bookshelfData struct {
	Shelves []struct {
		ShelfID string `json:"shelf_id"`
		Books   []struct {
			BookID      int `json:"book_id"`
			SourceBoxes []struct {
				CropImage string `json:"crop_image"`
			} `json:"source_boxes"`
		} `json:"books"`
	} `json:"shelves"`
}

func (h *Handler) attachCropURLs(candidates []db.ShelfCandidateRow) {
	outputsDir := filepath.Clean(filepath.Join(h.JobsDir, ".."))
	dataPath := filepath.Join(outputsDir, "book_catalog_data_260702", "bookshelf_data.json")
	raw, err := os.ReadFile(dataPath)
	if err != nil {
		return
	}
	var data bookshelfData
	if err := json.Unmarshal(raw, &data); err != nil {
		return
	}

	crops := map[string]string{}
	for _, shelf := range data.Shelves {
		for _, book := range shelf.Books {
			if len(book.SourceBoxes) == 0 || book.SourceBoxes[0].CropImage == "" {
				continue
			}
			crop := filepath.Clean(book.SourceBoxes[0].CropImage)
			if !filepath.IsAbs(crop) {
				crop = filepath.Join(h.Jobs.RepoRoot, crop)
			}
			if rel, ok := outputRelativePath(outputsDir, crop); ok {
				key := fmt.Sprintf("%d:%s", book.BookID, shelf.ShelfID)
				crops[key] = rel
			}
		}
	}

	for i := range candidates {
		key := fmt.Sprintf("%d:%s", candidates[i].BookID, candidates[i].ShelfID)
		rel, ok := crops[key]
		if !ok {
			continue
		}
		candidates[i].CropImage = rel
		candidates[i].CropURL = "/static/" + strings.ReplaceAll(rel, string(filepath.Separator), "/")
	}
}

func outputRelativePath(outputsDir string, path string) (string, bool) {
	rel, err := filepath.Rel(outputsDir, path)
	if err != nil || rel == "." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || rel == ".." {
		return "", false
	}
	return rel, true
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

func (h *Handler) DetectTags(c *gin.Context) {
	if h.TagMap == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "apriltag map is not configured"})
		return
	}
	file, header, err := c.Request.FormFile("image")
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "image field required"})
		return
	}
	defer file.Close()

	ext := filepath.Ext(header.Filename)
	if ext == "" {
		ext = ".jpg"
	}
	tmp, err := os.CreateTemp("", "booksearch-tag-*"+ext)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	tmpPath := tmp.Name()
	defer os.Remove(tmpPath)
	if _, err := io.Copy(tmp, file); err != nil {
		tmp.Close()
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	if err := tmp.Close(); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	repoRoot := ""
	if h.Jobs != nil {
		repoRoot = h.Jobs.RepoRoot
	}
	if repoRoot == "" {
		repoRoot = "."
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), 8*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "uv", "run", "python", "scripts/detect_apriltags.py", "--image", tmpPath, "--map", h.TagMap)
	cmd.Dir = repoRoot
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if ctx.Err() == context.DeadlineExceeded {
		c.JSON(http.StatusGatewayTimeout, gin.H{"error": "tag detection timed out"})
		return
	}
	if err != nil {
		msg := stderr.String()
		if msg == "" {
			msg = err.Error()
		}
		c.JSON(http.StatusInternalServerError, gin.H{"error": msg})
		return
	}

	var payload any
	if err := json.Unmarshal(out, &payload); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "invalid detector response"})
		return
	}
	c.JSON(http.StatusOK, payload)
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
