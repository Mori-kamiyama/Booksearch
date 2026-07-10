package main

import (
	"os"
	"path/filepath"
	"runtime"
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
}

func defaultConfig() Config {
	// ここでは backend/ の 1 つ上がリポジトリルート
	_, filename, _, _ := runtime.Caller(0)
	repoRoot := filepath.Dir(filepath.Dir(filename))

	return Config{
		RepoRoot:    repoRoot,
		LibraryDB:   filepath.Join(repoRoot, "outputs", "library", "library.db"),
		OutputsDir:  filepath.Join(repoRoot, "outputs"),
		JobsDir:     filepath.Join(repoRoot, "outputs", "jobs"),
		YOLOModel:   filepath.Join(repoRoot, "runs", "detect", "runs", "picture_box_detection", "yolo11n_quick", "weights", "best.pt"),
		// 生成済みのライブラリ全体マップを既定で使う。--apriltag-map で上書き可能。
		AprilTagMap: filepath.Join(repoRoot, "data", "apriltag_library_map.json"),
		Host:        envOr("HOST", "127.0.0.1"),
		Port:        envOr("PORT", "8080"),
	}
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
