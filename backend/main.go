package main

import (
	"flag"
	"fmt"
	"log"
	"os"

	"booksearch/backend/internal/db"
	"booksearch/backend/internal/handler"
	"booksearch/backend/internal/job"

	"github.com/gin-gonic/gin"
)

func main() {
	cfg := defaultConfig()

	flag.StringVar(&cfg.Host, "host", cfg.Host, "ホスト")
	flag.StringVar(&cfg.Port, "port", cfg.Port, "ポート")
	flag.StringVar(&cfg.LibraryDB, "library-db", cfg.LibraryDB, "library.db パス")
	flag.StringVar(&cfg.YOLOModel, "model", cfg.YOLOModel, "YOLO モデルパス")
	flag.StringVar(&cfg.AprilTagMap, "apriltag-map", cfg.AprilTagMap, "AprilTag マッピング JSON")
	flag.Float64Var(&cfg.MaxTagDistance, "max-tag-distance", 0, "tag 検出最大距離px (0=auto)")
	flag.Parse()

	if _, err := os.Stat(cfg.LibraryDB); os.IsNotExist(err) {
		log.Printf("警告: library.db が見つかりません: %s", cfg.LibraryDB)
		log.Println("  uv run python scripts/build_library_db.py を先に実行してください")
	}

	store, err := db.Open(cfg.LibraryDB)
	if err != nil {
		log.Fatalf("DB を開けません: %v", err)
	}
	defer store.Close()

	if err := os.MkdirAll(cfg.JobsDir, 0o755); err != nil {
		log.Fatalf("jobs ディレクトリを作れません: %v", err)
	}

	jobs := &job.Manager{
		JobsDir:        cfg.JobsDir,
		RepoRoot:       cfg.RepoRoot,
		Model:          cfg.YOLOModel,
		LibraryDB:      cfg.LibraryDB,
		TagMap:         cfg.AprilTagMap,
		MaxTagDistance: cfg.MaxTagDistance,
	}

	h := &handler.Handler{
		Store:   store,
		Jobs:    jobs,
		JobsDir: cfg.JobsDir,
		TagMap:  cfg.AprilTagMap,
	}

	gin.SetMode(gin.ReleaseMode)
	r := buildRouter(h)

	addr := fmt.Sprintf("%s:%s", cfg.Host, cfg.Port)
	log.Printf("API server: http://%s/api/", addr)
	log.Printf("DB        : %s", cfg.LibraryDB)
	if cfg.AprilTagMap != "" {
		log.Printf("AprilTag  : %s", cfg.AprilTagMap)
	}
	if err := r.Run(addr); err != nil {
		log.Fatalf("サーバー起動失敗: %v", err)
	}
}
