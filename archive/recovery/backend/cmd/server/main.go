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
		JobsDir:  cfg.JobsDir,
		RepoRoot: cfg.RepoRoot,
		Model:    cfg.YOLOModel,
		TagMap:   cfg.AprilTagMap,
	}

	h := &handler.Handler{
		Store:   store,
		Jobs:    jobs,
		JobsDir: cfg.JobsDir,
		TagMap:  cfg.AprilTagMap,
	}

	gin.SetMode(gin.ReleaseMode)
	r := gin.Default()

	// CORS（開発時に React dev server からアクセスできるよう）
	r.Use(func(c *gin.Context) {
		c.Header("Access-Control-Allow-Origin", "*")
		c.Header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
		c.Header("Access-Control-Allow-Headers", "Content-Type")
		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(204)
			return
		}
		c.Next()
	})

	api := r.Group("/api")
	{
		api.GET("/health", h.Health)
		api.GET("/books/search", h.SearchBooks)
		api.GET("/books/:id", h.GetBook)
		api.GET("/shelves", h.GetShelves)
		api.POST("/scan", h.Scan)
		api.GET("/jobs/:id", h.GetJob)
	}

	// outputs/ 配下の静的ファイル（crop, preview 画像）を配信
	r.GET("/static/*path", h.ServeStatic)

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
