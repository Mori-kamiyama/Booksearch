package job

import (
	"path/filepath"
	"slices"
	"testing"
	"time"
)

func TestCommandArgsImage(t *testing.T) {
	m := Manager{
		JobsDir:        "/tmp/jobs",
		Model:          "/models/best.pt",
		LibraryDB:      "/data/library.db",
		TagMap:         "/data/tags.json",
		MaxTagDistance: 123.4,
	}

	args := m.commandArgs("job-1", "/uploads/photo.jpg")

	want := []string{
		"run", "python", "scripts/build_book_catalog.py",
		"--source", "/uploads/photo.jpg",
		"--output-dir", filepath.Join("/tmp/jobs", "job-1"),
		"--model", "/models/best.pt",
		"--catalog-name", "catalog.json",
		"--library-db", "/data/library.db",
		"--apriltag-map", "/data/tags.json",
		"--max-tag-distance", "123.4",
	}
	if !slices.Equal(args, want) {
		t.Fatalf("args mismatch\nwant: %#v\n got: %#v", want, args)
	}
}

func TestCommandArgsVideoExtensionIsCaseInsensitive(t *testing.T) {
	m := Manager{
		JobsDir:   "/tmp/jobs",
		Model:     "/models/best.pt",
		LibraryDB: "/data/library.db",
	}

	args := m.commandArgs("job-1", "/uploads/scan.MOV")

	if !slices.Contains(args, "--video") {
		t.Fatalf("want --video for uppercase MOV, got %#v", args)
	}
	if slices.Contains(args, "--source") {
		t.Fatalf("did not expect --source for video, got %#v", args)
	}
}

func TestStartPreservesCreatedAt(t *testing.T) {
	dir := t.TempDir()
	createdAt := time.Date(2026, 5, 29, 9, 0, 0, 0, time.UTC)
	m := Manager{JobsDir: dir, RepoRoot: dir, Model: "/missing.pt"}

	if err := m.Create("job-1"); err != nil {
		t.Fatal(err)
	}
	if err := m.writeState("job-1", JobState{ID: "job-1", Status: StatusPending, CreatedAt: createdAt}); err != nil {
		t.Fatal(err)
	}

	m.Start("job-1", "/uploads/photo.jpg")

	state, err := m.GetState("job-1")
	if err != nil {
		t.Fatal(err)
	}
	if !state.CreatedAt.Equal(createdAt) {
		t.Fatalf("created_at changed: want %s, got %s", createdAt, state.CreatedAt)
	}
}