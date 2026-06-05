package job_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"booksearch/backend/internal/job"
)

func newManager(t *testing.T) *job.Manager {
	t.Helper()
	return &job.Manager{
		JobsDir:  t.TempDir(),
		RepoRoot: t.TempDir(),
		Model:    "nonexistent.pt",
	}
}

// ── Create / GetState ────────────────────────────────────────

func TestCreate_WritesStatusFile(t *testing.T) {
	m := newManager(t)

	if err := m.Create("job1"); err != nil {
		t.Fatalf("Create: %v", err)
	}

	statusPath := filepath.Join(m.JobsDir, "job1", "status.json")
	data, err := os.ReadFile(statusPath)
	if err != nil {
		t.Fatalf("status.json not found: %v", err)
	}

	var state job.JobState
	if err := json.Unmarshal(data, &state); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if state.ID != "job1" {
		t.Errorf("want id=job1, got %q", state.ID)
	}
	if state.Status != job.StatusPending {
		t.Errorf("want pending, got %q", state.Status)
	}
}

func TestCreate_MakesJobDirectory(t *testing.T) {
	m := newManager(t)

	m.Create("job2")

	jobDir := filepath.Join(m.JobsDir, "job2")
	if info, err := os.Stat(jobDir); err != nil || !info.IsDir() {
		t.Errorf("expected job directory to exist: %v", err)
	}
}

func TestGetState_NotFound(t *testing.T) {
	m := newManager(t)

	state, err := m.GetState("nonexistent")
	if err != nil {
		t.Fatal(err)
	}
	if state != nil {
		t.Errorf("want nil, got %+v", state)
	}
}

func TestGetState_AfterCreate(t *testing.T) {
	m := newManager(t)
	m.Create("job3")

	state, err := m.GetState("job3")
	if err != nil {
		t.Fatalf("GetState: %v", err)
	}
	if state == nil {
		t.Fatal("want state, got nil")
	}
	if state.Status != job.StatusPending {
		t.Errorf("want pending, got %q", state.Status)
	}
	if state.CreatedAt.IsZero() {
		t.Error("want non-zero CreatedAt")
	}
}

// ── GetCatalog ───────────────────────────────────────────────

func TestGetCatalog_NotFound(t *testing.T) {
	m := newManager(t)
	m.Create("job4")

	catalog, err := m.GetCatalog("job4")
	if err != nil {
		t.Fatalf("GetCatalog: %v", err)
	}
	if catalog != nil {
		t.Errorf("want nil catalog (file not written yet), got %v", catalog)
	}
}

func TestGetCatalog_Present(t *testing.T) {
	m := newManager(t)
	m.Create("job5")

	// catalog.json を手動で書き込む（OCR パイプライン完了後に相当）
	catalogData := map[string]any{
		"source":       "test/frames",
		"gemini_model": "gemini-3.1-flash-lite-preview",
		"entries": []any{
			map[string]any{
				"box_id":    "frame_000000:box_01",
				"shelf_id":  "shelf-A-01",
				"ocr_error": nil,
				"books": []any{
					map[string]any{"title": "Go言語プログラミング"},
				},
			},
		},
	}
	raw, _ := json.Marshal(catalogData)
	os.WriteFile(filepath.Join(m.JobsDir, "job5", "catalog.json"), raw, 0o644)

	catalog, err := m.GetCatalog("job5")
	if err != nil {
		t.Fatalf("GetCatalog: %v", err)
	}
	if catalog == nil {
		t.Fatal("want catalog, got nil")
	}

	entries, _ := catalog["entries"].([]any)
	if len(entries) != 1 {
		t.Fatalf("want 1 entry, got %d", len(entries))
	}
	e, _ := entries[0].(map[string]any)
	if e["shelf_id"] != "shelf-A-01" {
		t.Errorf("want shelf_id=shelf-A-01, got %v", e["shelf_id"])
	}
	books, _ := e["books"].([]any)
	if len(books) != 1 {
		t.Fatalf("want 1 book, got %d", len(books))
	}
	b, _ := books[0].(map[string]any)
	if b["title"] != "Go言語プログラミング" {
		t.Errorf("wrong title: %v", b["title"])
	}
}

// TestGetCatalog_OcrError: ocr_error フィールドがある場合も正しく返す。
func TestGetCatalog_OcrError(t *testing.T) {
	m := newManager(t)
	m.Create("job6")

	catalogData := map[string]any{
		"entries": []any{
			map[string]any{
				"box_id":    "frame_000001:box_01",
				"shelf_id":  nil,
				"ocr_error": "skipped_duplicate_crop",
				"books":     []any{},
			},
		},
	}
	raw, _ := json.Marshal(catalogData)
	os.WriteFile(filepath.Join(m.JobsDir, "job6", "catalog.json"), raw, 0o644)

	catalog, err := m.GetCatalog("job6")
	if err != nil {
		t.Fatal(err)
	}
	entries, _ := catalog["entries"].([]any)
	e, _ := entries[0].(map[string]any)
	if e["ocr_error"] != "skipped_duplicate_crop" {
		t.Errorf("want ocr_error=skipped_duplicate_crop, got %v", e["ocr_error"])
	}
}

// ── Start によるジョブ状態遷移 ──────────────────────────────

// TestStart_MovesToTerminalState: Start() を呼ぶとゴルーチンが走り、
// subprocess が失敗してもジョブは terminal (done|failed) になる。
func TestStart_MovesToTerminalState(t *testing.T) {
	m := newManager(t)
	m.Create("job7")

	m.Start("job7", "/nonexistent/upload.jpg")

	deadline := time.Now().Add(10 * time.Second)
	var finalStatus job.Status
	for time.Now().Before(deadline) {
		state, err := m.GetState("job7")
		if err != nil || state == nil {
			time.Sleep(50 * time.Millisecond)
			continue
		}
		if state.Status == job.StatusDone || state.Status == job.StatusFailed {
			finalStatus = state.Status
			break
		}
		time.Sleep(50 * time.Millisecond)
	}

	if finalStatus == "" {
		t.Fatal("job did not reach terminal state within 10s")
	}
	t.Logf("job7 finished with status=%s", finalStatus)

	// 失敗した場合は error フィールドが記録されているはず
	if finalStatus == job.StatusFailed {
		state, _ := m.GetState("job7")
		if state.Error == "" {
			t.Error("failed job should have non-empty Error field")
		}
	}
}
