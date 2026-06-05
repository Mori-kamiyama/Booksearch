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
				"ocr_error":           "skipped_duplicate_crop",
				"books":               []any{},
			},
		},
	}
}

// ── ヘルスチェック ──────────────────────────────────────────

func TestHealth(t *testing.T) {
	env := setupTestEnv(t)
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
			t.Fatalf("want 404, got %d", resp.StatusCode)
		}
	})

	t.Run("invalid_id", func(t *testing.T) {
		resp, err := http.Get(env.server.URL + "/api/books/abc")
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusBadRequest {
			t.Fatalf("want 400, got %d", resp.StatusCode)
		}
	})
}

// ── スキャンジョブのライフサイクル ───────────────────────────

func TestScanJobLifecycle(t *testing.T) {
	env := setupTestEnv(t)

	// ダミー画像でマルチパートリクエストを組み立てる
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	fw, err := mw.CreateFormFile("image", "test.jpg")
	if err != nil {
		t.Fatal(err)
	}
	fw.Write([]byte("dummy"))
	mw.Close()

	// POST /api/scan → 202 + job_id
	resp, err := http.Post(env.server.URL+"/api/scan", mw.FormDataContentType(), &buf)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusAccepted {
		t.Fatalf("want 202, got %d", resp.StatusCode)
	}
	var scanBody map[string]string
	json.NewDecoder(resp.Body).Decode(&scanBody)
	jobID := scanBody["job_id"]
	if jobID == "" {
		t.Fatal("want job_id in response")
	}

	// GET /api/jobs/:id → ジョブが terminal になるまでポーリング
	deadline := time.Now().Add(15 * time.Second)
	var finalStatus string
	for time.Now().Before(deadline) {
		r, err := http.Get(fmt.Sprintf("%s/api/jobs/%s", env.server.URL, jobID))
		if err != nil {
			t.Fatal(err)
		}
		var jobBody map[string]any
		json.NewDecoder(r.Body).Decode(&jobBody)
		r.Body.Close()

		status, _ := jobBody["status"].(string)
		if status == "done" || status == "failed" {
			finalStatus = status
			break
		}
		time.Sleep(200 * time.Millisecond)
	}
	if finalStatus == "" {
		t.Fatal("job did not reach terminal state within 15s")
	}
	t.Logf("job %s finished with status=%s", jobID, finalStatus)
}

// TestScanBadRequest: image フィールドなしで POST すると 400
func TestScanBadRequest(t *testing.T) {
	env := setupTestEnv(t)

	resp, err := http.Post(
		env.server.URL+"/api/scan",
		"multipart/form-data; boundary=boundary123",
		strings.NewReader("--boundary123--"),
	)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("want 400, got %d", resp.StatusCode)
	}
}

func TestGetJobNotFound(t *testing.T) {
	env := setupTestEnv(t)
	resp, err := http.Get(env.server.URL + "/api/jobs/nonexistent-id")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("want 404, got %d", resp.StatusCode)
	}
}

// ── OCR カタログ結果の読み出し ───────────────────────────────

// TestJobDoneWithCatalog: status=done のジョブに catalog.json があれば
// GET /api/jobs/:id がカタログ内容を返す。
func TestJobDoneWithCatalog(t *testing.T) {
	env := setupTestEnv(t)

	jobID := "test-done-catalog"
	jobDir := filepath.Join(env.jobsDir, jobID)
	os.MkdirAll(jobDir, 0o755)

	stateJSON, _ := json.Marshal(map[string]any{
		"id":         jobID,
		"status":     "done",
		"created_at": time.Now().Format(time.RFC3339),
	})
	os.WriteFile(filepath.Join(jobDir, "status.json"), stateJSON, 0o644)

	catalogJSON, _ := json.Marshal(buildTestCatalog())
	os.WriteFile(filepath.Join(jobDir, "catalog.json"), catalogJSON, 0o644)

	resp, err := http.Get(env.server.URL + "/api/jobs/" + jobID)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("want 200, got %d", resp.StatusCode)
	}

	var body map[string]any
	json.NewDecoder(resp.Body).Decode(&body)

	if body["status"] != "done" {
		t.Errorf("want status=done, got %v", body["status"])
	}
	catalogResp, ok := body["catalog"].(map[string]any)
	if !ok {
		t.Fatalf("want catalog object in response, got %T", body["catalog"])
	}

	entries, _ := catalogResp["entries"].([]any)
	if len(entries) != 2 {
		t.Fatalf("want 2 entries, got %d", len(entries))
	}

	// entry[0]: OCR 成功、shelf_id あり、本あり
	e0, _ := entries[0].(map[string]any)
	if e0["shelf_id"] != "shelf-A-01" {
		t.Errorf("entry[0] shelf_id: want shelf-A-01, got %v", e0["shelf_id"])
	}
	books0, _ := e0["books"].([]any)
	if len(books0) != 1 {
		t.Errorf("entry[0] want 1 book, got %d", len(books0))
	}
	b0, _ := books0[0].(map[string]any)
	if b0["title"] != "Go言語プログラミング" {
		t.Errorf("entry[0].books[0].title: got %v", b0["title"])
	}
	// book_lookup の候補スコアを確認
	lookup, _ := b0["book_lookup"].(map[string]any)
	candidates, _ := lookup["candidates"].([]any)
	if len(candidates) == 0 {
		t.Error("want at least 1 lookup candidate")
	}
	c0, _ := candidates[0].(map[string]any)
	score, _ := c0["score"].(float64)
	if score < 0.9 {
		t.Errorf("want score≥0.9, got %v", score)
	}

	// entry[1]: 動画重複スキップ、本なし
	e1, _ := entries[1].(map[string]any)
	if e1["ocr_error"] != "skipped_duplicate_crop" {
		t.Errorf("entry[1] ocr_error: want skipped_duplicate_crop, got %v", e1["ocr_error"])
	}
	books1, _ := e1["books"].([]any)
	if len(books1) != 0 {
		t.Errorf("entry[1] want 0 books, got %d", len(books1))
	}
	if e1["shelf_id"] != nil {
		t.Errorf("entry[1] want shelf_id=nil, got %v", e1["shelf_id"])
	}
}

// TestJobFailedHasError: status=failed のジョブにはエラーメッセージが含まれる。
func TestJobFailedHasError(t *testing.T) {
	env := setupTestEnv(t)

	jobID := "test-failed-job"
	jobDir := filepath.Join(env.jobsDir, jobID)
	os.MkdirAll(jobDir, 0o755)

	stateJSON, _ := json.Marshal(map[string]any{
		"id":         jobID,
		"status":     "failed",
		"created_at": time.Now().Format(time.RFC3339),
		"error":      "pipeline failed: exit status 1",
	})
	os.WriteFile(filepath.Join(jobDir, "status.json"), stateJSON, 0o644)

	resp, err := http.Get(env.server.URL + "/api/jobs/" + jobID)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("want 200, got %d", resp.StatusCode)
	}

	var body map[string]any
	json.NewDecoder(resp.Body).Decode(&body)

	if body["status"] != "failed" {
		t.Errorf("want status=failed, got %v", body["status"])
	}
	if body["error"] == nil || body["error"] == "" {
		t.Error("want error field for failed job")
	}
	if _, ok := body["catalog"]; ok {
		t.Error("failed job should not have catalog field")
	}
}

// ── 静的ファイル配信 ─────────────────────────────────────────

// TestStaticFileServe: outputs/ 配下のファイルを GET /static/ で取得できる。
func TestStaticFileServe(t *testing.T) {
	env := setupTestEnv(t)

	content := "static file content"
	os.WriteFile(filepath.Join(env.outDir, "test-static.txt"), []byte(content), 0o644)

	resp, err := http.Get(env.server.URL + "/static/test-static.txt")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("want 200, got %d", resp.StatusCode)
	}

	var body bytes.Buffer
	body.ReadFrom(resp.Body)
	if body.String() != content {
		t.Errorf("want %q, got %q", content, body.String())
	}
}

// TestStaticPathTraversal: ディレクトリトラバーサルは 403 を返す。
func TestStaticPathTraversal(t *testing.T) {
	env := setupTestEnv(t)

	resp, err := http.Get(env.server.URL + "/static/../../etc/passwd")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("want 403, got %d", resp.StatusCode)
	}
}

// TestStaticNotFound: 存在しないファイルは 404。
func TestStaticNotFound(t *testing.T) {
	env := setupTestEnv(t)

	resp, err := http.Get(env.server.URL + "/static/does-not-exist.jpg")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("want 404, got %d", resp.StatusCode)
	}
}

// ── 棚マップ ─────────────────────────────────────────────────

func TestGetShelves(t *testing.T) {
	t.Run("no_map", func(t *testing.T) {
		env := setupTestEnv(t)
		resp, err := http.Get(env.server.URL + "/api/shelves")
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("want 200, got %d", resp.StatusCode)
		}
		var body map[string]any
		json.NewDecoder(resp.Body).Decode(&body)
		if _, ok := body["shelves"]; !ok {
			t.Error("want shelves key in response")
		}
	})

	t.Run("with_map", func(t *testing.T) {
		tmpDir := t.TempDir()
		mapFile := filepath.Join(tmpDir, "map.json")
		os.WriteFile(mapFile, []byte(`{"0":{"shelf_id":"A-01"}}`), 0o644)

		jobsDir := filepath.Join(tmpDir, "jobs")
		os.MkdirAll(jobsDir, 0o755)
		store := openTestStore(t, tmpDir)
		t.Cleanup(func() { store.Close() })

		h := &handler.Handler{
			Store:   store,
			Jobs:    &job.Manager{JobsDir: jobsDir},
			JobsDir: jobsDir,
			TagMap:  mapFile,
		}
		srv := httptest.NewServer(buildRouter(h))
		t.Cleanup(srv.Close)

		resp, err := http.Get(srv.URL + "/api/shelves")
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("want 200, got %d", resp.StatusCode)
		}
		ct := resp.Header.Get("Content-Type")
		if !strings.Contains(ct, "application/json") {
			t.Errorf("want application/json, got %s", ct)
		}
		var shelfMap map[string]any
		json.NewDecoder(resp.Body).Decode(&shelfMap)
		if shelfMap["0"] == nil {
			t.Error("want tag 0 in shelf map response")
		}
	})
}
