package main

import (
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"time"
)

type Config struct {
	RepoRoot       string
	LibraryDB      string
	OutputsDir     string
	JobsDir        string
	YOLOModel      string
	AprilTagMap    string // optional
	MaxTagDistance float64
	Host           string
	Port           string
	MaxConcurrent  int
	JobTimeout     time.Duration
}

func defaultConfig() Config {
	// app/backend/ の 2 つ上がリポジトリルート
	_, filename, _, _ := runtime.Caller(0)
	repoRoot := filepath.Dir(filepath.Dir(filepath.Dir(filename)))

	return Config{
		RepoRoot:   repoRoot,
		LibraryDB:  filepath.Join(repoRoot, "research", "outputs", "library", "library.db"),
		OutputsDir: filepath.Join(repoRoot, "research", "outputs"),
		JobsDir:    filepath.Join(repoRoot, "research", "outputs", "jobs"),
		YOLOModel:  filepath.Join(repoRoot, "research", "runs", "detect", "runs", "picture_box_detection", "yolo11n_quick", "weights", "best.pt"),
		// 生成済みのライブラリ全体マップを既定で使う。--apriltag-map で上書き可能。
		AprilTagMap:   filepath.Join(repoRoot, "assets", "data", "apriltag_library_map.json"),
		Host:          envOr("HOST", "127.0.0.1"),
		Port:          envOr("PORT", "8080"),
		MaxConcurrent: envInt("MAX_CONCURRENT_JOBS", 1),
		JobTimeout:    time.Duration(envInt("JOB_TIMEOUT_SECONDS", 600)) * time.Second,
	}
}

func envInt(key string, fallback int) int {
	if value, err := strconv.Atoi(os.Getenv(key)); err == nil && value > 0 {
		return value
	}
	return fallback
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func absFlagPath(path *string) {
	if *path == "" || filepath.IsAbs(*path) {
		return
	}
	if abs, err := filepath.Abs(*path); err == nil {
		*path = abs
	}
}
