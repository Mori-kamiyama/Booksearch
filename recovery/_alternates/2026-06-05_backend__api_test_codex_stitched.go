package main

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"booksearch/backend/internal/db"
	"booksearch/backend/internal/handler"
	"booksearch/backend/internal/job"

	"github.com/gin-gonic/gin"
)

func TestMain(m *testing.M) {
	gin.SetMode(gin.TestMode)
	os.Exit(m.Run())
}

// testEnv はテスト用 HTTP サーバー・ジョブディレクトリ・出力ルートをまとめる。
type testEnv struct {
	server  *httptest.Server
	jobsDir string
	outDir  string // static 配信のルートディレクトリ（jobsDir の親）
}

func setupTestEnv(t *testing.T) *testEnv {
	t.Helper()

	tmpDir := t.TempDir()
	jobsDir := filepath.Join(tmpDir, "jobs")
	if err := os.MkdirAll(jobsDir, 0o755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}

	store := openTestStore(t, tmpDir)

	jobs := &job.Manager{
		JobsDir:  jobsDir,
		RepoRoot: tmpDir,
		Model:    filepath.Join(tmpDir, "nonexistent.pt"),
	}

	h := &handler.Handler{
		Store:   store,
		Jobs:    jobs,
		JobsDir: jobsDir,
		TagMap:  "",
	}

	srv := httptest.NewServer(buildRouter(h))
	t.Cleanup(func() {
		srv.Close()
		store.Close()
	})
	return &testEnv{server: srv, jobsDir: jobsDir, outDir: tmpDir}
}

// openTestStore は一時 SQLite DB を作り 2 冊のシードデータを投入して Store を返す。
func openTestStore(t *testing.T, dir string) *db.Store {
	t.Helper()
	dbPath := filepath.Join(dir, "test.db")

	raw, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatalf("sql.Open: %v", err)
	}
	if err := db.CreateSchema(raw); err != nil {
		t.Fatalf("CreateSchema: %v", err)
	}

	// 1冊目: タイトル検索・ID 取得テスト用
	res, err := raw.Exec(
		`INSERT INTO books (title, authors, publisher, isbn, title_norm, authors_norm, isbn_norm)
		 VALUES (?, ?, ?, ?, ?, ?, ?)`,
		"Go言語プログラミング", "山田太郎", "技術書院", "9781234567890",
		"go言語プログラミング", "山田太郎", "9781234567890",
	)
	if err != nil {
		t.Fatalf("seed books[0]: %v", err)
	}
	id1, _ := res.LastInsertId()
	raw.Exec(`INSERT INTO book_covers (book_id, thumbnail, info_link) VALUES (?, NULL, NULL)`, id1)

	// 2冊目: 著者・ISBN 検索テスト用
	res2, err := raw.Exec(
		`INSERT INTO books (title, authors, publisher, isbn, title_norm, authors_norm, isbn_norm)
		 VALUES (?, ?, ?, ?, ?, ?, ?)`,
		"Pythonプログラミング入門", "佐藤花子", "プログラム出版", "9789876543210",
		"pythonプログラミング入門", "佐藤花子", "9789876543210",
	)
	if err != nil {
		t.Fatalf("seed books[1]: %v", err)
	}
	id2, _ := res2.LastInsertId()
	raw.Exec(`INSERT INTO book_covers (book_id, thumbnail, info_link) VALUES (?, NULL, NULL)`, id2)
	raw.Close()

	store, err := db.Open(dbPath)
	if err != nil {
		t.Fatalf("db.Open: %v", err)
	}
	t.Cleanup(func() { store.Close() })
	return store
}

// buildTestCatalog は OCR パイプラインが出力するカタログ JSON の最小限の例を返す。
// entry[0]: OCR 成功・shelf_id あり
// entry[1]: 動画重複スキップによる ocr_error あり
func buildTestCatalog() map[string]any {
	return map[string]any{
		"source":         "test/frames",
		"detector_model": "best.pt",
		"gemini_model":   "gemini-3.1-flash-lite-preview",
		"book_lookup":    true,
		"library_db":     "library.db",
		"shelf_mapping":  nil,
		"entries": []any{
			map[string]any{
				"box_id":              "frame_000000:box_01",
				"source_image":        "test/frames/frame_000000.jpg",
				"crop_image":          "test/crops/frame_000000_box_01.jpg",
				"detector_confidence": 0.87,
				"bbox_xyxy":           []int{100, 200, 300, 400},
				"crop_quality":        nil,
				"shelf_id":            "shelf-A-01",
				"shelf_assignment": map[string]any{
					"shelf_id": "shelf-A-01",
					"status":   "assigned",
				},
				"ocr_error": nil,
				"books": []any{
					map[string]any{
						"title": "Go言語プログラミング",
						"book_lookup": map[string]any{
							"source": "library_db",
							"query":  "Go言語プログラミング",
							"candidates": []any{
								map[string]any{
									"score":            0.95,
									"match_confidence": "auto",
									"title":            "Go言語プログラミング",
									"authors":          []string{"山田太郎"},
									"library_db_id":    1,
								},
							},
						},
					},
				},
			},
			map[string]any{
				"box_id":              "frame_000001:box_01",
				"source_image":        "test/frames/frame_000001.jpg",
				"crop_image":          "test/crops/frame_000001_box_01.jpg",
				"detector_confidence": 0.65,
				"bbox_xyxy":           []int{100, 400, 300, 600},
				"crop_quality":        nil,
				"shelf_id":            nil,
				"shelf_assignment":    nil,
				"ocr_error":          "skipped_duplicate_crop",
				"books":              []any{},

// ── 本の個別取得 ─────────────────────────────────────────────

func TestGetBook(t *testing.T) {
	env := setupTestEnv(t)

	t.Run("found", func(t *testing.T) {
		resp, err := http.Get(env.server.URL + "/api/books/1")
		if err != nil {
	resp, err := http.Get(env.server.URL + "/api/health")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("want 200, got %d", resp.StatusCode)
	}
	var body map[string]any
	json.NewDecoder(resp.Body).Decode(&body)
	if body["status"] != "ok" {
		t.Errorf("want status=ok, got %v", body["status"])
	}
}

// ── 本の検索 ────────────────────────────────────────────────

func TestBookSearch(t *testing.T) {
	env := setupTestEnv(t)

	t.Run("hit_by_title", func(t *testing.T) {
		resp, err := http.Get(env.server.URL + "/api/books/search?q=Go言語")
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("want 200, got %d", resp.StatusCode)
		}
		var body map[string]any
		json.NewDecoder(resp.Body).Decode(&body)
		books, _ := body["books"].([]any)
		if len(books) == 0 {
			t.Error("want ≥1 book, got 0")
		}
	})

	t.Run("hit_by_author", func(t *testing.T) {
		resp, err := http.Get(env.server.URL + "/api/books/search?q=佐藤花子")
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("want 200, got %d", resp.StatusCode)
		}
		var body map[string]any
		json.NewDecoder(resp.Body).Decode(&body)
		books, _ := body["books"].([]any)
		if len(books) != 1 {
			t.Fatalf("want 1, got %d", len(books))
		}
		b, _ := books[0].(map[string]any)
		if b["title"] != "Pythonプログラミング入門" {
			t.Errorf("wrong title: %v", b["title"])
		}
	})

	t.Run("hit_by_isbn", func(t *testing.T) {
		resp, err := http.Get(env.server.URL + "/api/books/search?q=9789876543210")
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("want 200, got %d", resp.StatusCode)
		}
		var body map[string]any
		json.NewDecoder(resp.Body).Decode(&body)
		books, _ := body["books"].([]any)
		if len(books) != 1 {
			t.Fatalf("want 1, got %d", len(books))
		}
	})

	t.Run("limit", func(t *testing.T) {
		// 両方の本に "プログラミング" が含まれる → limit=1 で 1 件に絞られる
		resp, err := http.Get(env.server.URL + "/api/books/search?q=プログラミング&limit=1")
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("want 200, got %d", resp.StatusCode)
		}
		var body map[string]any
		json.NewDecoder(resp.Body).Decode(&body)
		books, _ := body["books"].([]any)
		if len(books) != 1 {
			t.Errorf("want 1 (limited), got %d", len(books))
		}
	})

	t.Run("miss", func(t *testing.T) {
		resp, err := http.Get(env.server.URL + "/api/books/search?q=xyznotexist")
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("want 200, got %d", resp.StatusCode)
		}
		var body map[string]any
		json.NewDecoder(resp.Body).Decode(&body)
		books, _ := body["books"].([]any)
		if len(books) != 0 {
			t.Errorf("want 0, got %d", len(books))
		}
	})

	t.Run("missing_q", func(t *testing.T) {
		resp, err := http.Get(env.server.URL + "/api/books/search")
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusBadRequest {
			t.Fatalf("want 400, got %d", resp.StatusCode)
		}
	})
}

// ── 本の個別取得 ─────────────────────────────────────────────

func TestGetBook(t *testing.T) {
	env := setupTestEnv(t)

	t.Run("found", func(t *testing.T) {
		resp, err := http.Get(env.server.URL + "/api/books/1")
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("want 200, got %d", resp.StatusCode)
		}
		var book map[string]any
		json.NewDecoder(resp.Body).Decode(&book)
		if book["title"] == nil {
			t.Error("want title field in response")
		}
	})

	t.Run("not_found", func(t *testing.T) {
		resp, err := http.Get(env.server.URL + "/api/books/9999")
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusNotFound {
		}
		ct := resp.Header.Get("Content-Type")
		if !strings.Contains(ct, "application/json") {
			t.Errorf("want application/json, got %s", ct)
		}
	})
}
