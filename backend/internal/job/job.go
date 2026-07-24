package job

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

type Status string

const (
	StatusPending  Status = "pending"
	StatusRunning  Status = "running"
	StatusDone     Status = "done"
	StatusFailed   Status = "failed"
	StatusCanceled Status = "canceled"
)

type JobState struct {
	ID        string    `json:"id"`
	Status    Status    `json:"status"`
	CreatedAt time.Time `json:"created_at"`
	Error     string    `json:"error,omitempty"`
}

type Manager struct {
	JobsDir        string
	RepoRoot       string
	Model          string
	LibraryDB      string
	TagMap         string // optional
	MaxTagDistance float64
}

func (m *Manager) jobDir(id string) string { return filepath.Join(m.JobsDir, id) }
func (m *Manager) stateFile(id string) string {
	return filepath.Join(m.jobDir(id), "status.json")
}
func (m *Manager) catalogFile(id string) string {
	return filepath.Join(m.jobDir(id), "catalog.json")
}

func (m *Manager) Create(id string) error {
	if err := os.MkdirAll(m.jobDir(id), 0o755); err != nil {
		return err
	}
	return m.writeState(id, JobState{ID: id, Status: StatusPending, CreatedAt: time.Now()})
}

func (m *Manager) Start(id, uploadPath string) {
	_ = m.writeState(id, JobState{ID: id, Status: StatusRunning, CreatedAt: time.Now()})
	go func() {
		err := m.run(id, uploadPath)
		state := JobState{ID: id, CreatedAt: time.Now()}
		if err != nil {
			state.Status = StatusFailed
			state.Error = err.Error()
		} else {
			state.Status = StatusDone
		}
		_ = m.writeState(id, state)
	}()
}

// StartFrames runs the normal catalog pipeline only after a live session is
// explicitly confirmed. Until then frames are inert files and incur no OCR.
func (m *Manager) StartFrames(id, framesDir string) {
	_ = m.writeState(id, JobState{ID: id, Status: StatusRunning, CreatedAt: time.Now()})
	go func() {
		err := m.runFrames(id, framesDir)
		state := JobState{ID: id, CreatedAt: time.Now(), Status: StatusDone}
		if err != nil {
			state.Status = StatusFailed
			state.Error = err.Error()
		}
		_ = m.writeState(id, state)
	}()
}

func (m *Manager) runFrames(id, framesDir string) error {
	args := []string{"run", "python", "scripts/build_book_catalog.py", "--source", framesDir, "--dedup-crops", "--fingerprint-db", filepath.Join(m.JobsDir, "..", "crop_fingerprints.db"), "--output-dir", m.jobDir(id), "--model", m.Model, "--library-db", m.LibraryDB, "--catalog-name", "catalog.json"}
	if m.TagMap != "" {
		args = append(args, "--apriltag-map", m.TagMap)
		if m.MaxTagDistance > 0 {
			args = append(args, "--max-tag-distance", fmt.Sprintf("%g", m.MaxTagDistance))
		}
	}
	cmd := exec.Command("uv", args...)
	cmd.Dir = m.RepoRoot
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("live pipeline failed: %w", err)
	}
	return nil
}

func (m *Manager) run(id, uploadPath string) error {
	args := []string{
		"run", "python", "scripts/build_book_catalog.py",
		"--source", uploadPath,
		"--output-dir", m.jobDir(id),
		"--model", m.Model,
		"--library-db", m.LibraryDB,
		"--catalog-name", "catalog.json",
	}
	if m.TagMap != "" {
		args = append(args, "--apriltag-map", m.TagMap)
		if m.MaxTagDistance > 0 {
			args = append(args, "--max-tag-distance", fmt.Sprintf("%g", m.MaxTagDistance))
		}
	}

	// 動画ファイルなら --video で渡す
	ext := filepath.Ext(uploadPath)
	for _, v := range []string{".mp4", ".mov", ".avi", ".mkv", ".webm"} {
		if ext == v {
			args = []string{
				"run", "python", "scripts/build_book_catalog.py",
				"--video", uploadPath,
				"--output-dir", m.jobDir(id),
				"--model", m.Model,
				"--library-db", m.LibraryDB,
				"--catalog-name", "catalog.json",
			}
			if m.TagMap != "" {
				args = append(args, "--apriltag-map", m.TagMap)
				if m.MaxTagDistance > 0 {
					args = append(args, "--max-tag-distance", fmt.Sprintf("%g", m.MaxTagDistance))
				}
			}
			break
		}
	}

	cmd := exec.Command("uv", args...)
	cmd.Dir = m.RepoRoot
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("pipeline failed: %w", err)
	}
	return nil
}

func (m *Manager) GetState(id string) (*JobState, error) {
	data, err := os.ReadFile(m.stateFile(id))
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var state JobState
	return &state, json.Unmarshal(data, &state)
}

func (m *Manager) GetCatalog(id string) (map[string]any, error) {
	data, err := os.ReadFile(m.catalogFile(id))
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var catalog map[string]any
	return catalog, json.Unmarshal(data, &catalog)
}

func (m *Manager) writeState(id string, state JobState) error {
	data, err := json.Marshal(state)
	if err != nil {
		return err
	}
	return os.WriteFile(m.stateFile(id), data, 0o644)
}
