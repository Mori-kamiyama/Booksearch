package job

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"time"
)

type Status string

const (
	StatusPending Status = "pending"
	StatusQueued  Status = "queued"
	StatusRunning Status = "running"
	StatusDone    Status = "done"
	StatusFailed  Status = "failed"
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
	MaxConcurrent  int
	JobTimeout     time.Duration
	RunCommand     func(context.Context, string, []string, string) error
	semOnce        sync.Once
	sem            chan struct{}
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
	_ = m.writeState(id, JobState{ID: id, Status: StatusQueued, CreatedAt: time.Now()})
	go func() {
		m.acquire()
		_ = m.writeState(id, JobState{ID: id, Status: StatusRunning, CreatedAt: time.Now()})
		ctx := context.Background()
		if m.JobTimeout > 0 {
			var cancel context.CancelFunc
			ctx, cancel = context.WithTimeout(ctx, m.JobTimeout)
			defer cancel()
		}
		err := m.runContext(ctx, id, uploadPath)
		m.release()
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

func (m *Manager) acquire() {
	m.semOnce.Do(func() {
		n := m.MaxConcurrent
		if n < 1 {
			n = 1
		}
		m.sem = make(chan struct{}, n)
	})
	m.sem <- struct{}{}
}
func (m *Manager) release() { <-m.sem }

func (m *Manager) run(id, uploadPath string) error {
	return m.runContext(context.Background(), id, uploadPath)
}

func (m *Manager) runContext(ctx context.Context, id, uploadPath string) error {
	args := []string{
		"run", "python", "research/scripts/build_book_catalog.py",
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
				"run", "python", "research/scripts/build_book_catalog.py",
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

	if m.RunCommand != nil {
		if err := m.RunCommand(ctx, "uv", args, m.RepoRoot); err != nil {
			return fmt.Errorf("pipeline failed: %w", err)
		}
		return nil
	}
	cmd := exec.CommandContext(ctx, "uv", args...)
	cmd.Dir = m.RepoRoot
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		if ctx.Err() != nil {
			return fmt.Errorf("pipeline timed out: %w", ctx.Err())
		}
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
