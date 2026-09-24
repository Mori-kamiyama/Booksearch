package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"

	"booksearch/backend/internal/db"
)

func main() {
	defaultDB := filepath.Join("..", "outputs", "library", "library.db")
	defaultJobsDir := filepath.Join("..", "outputs", "jobs")

	dbPath := flag.String("db", defaultDB, "library.db path")
	jobsDir := flag.String("jobs-dir", defaultJobsDir, "directory containing job catalog.json files")
	catalogGlob := flag.String("catalog-glob", "", "optional glob for catalog.json files")
	flag.Parse()

	store, err := db.Open(*dbPath)
	if err != nil {
		log.Fatalf("open db: %v", err)
	}
	defer store.Close()

	catalogs, err := findCatalogs(*jobsDir, *catalogGlob)
	if err != nil {
		log.Fatalf("find catalogs: %v", err)
	}

	totalUpdated := 0
	totalStats := catalogStats{}
	for _, catalogPath := range catalogs {
		catalog, err := readCatalog(catalogPath)
		if err != nil {
			log.Printf("skip %s: %v", catalogPath, err)
			continue
		}
		stats := summarizeCatalog(catalog)
		totalStats.add(stats)
		jobID := filepath.Base(filepath.Dir(catalogPath))
		updated, err := store.UpdateShelfConfidenceFromCatalog(jobID, catalog)
		if err != nil {
			log.Printf("skip %s: %v", catalogPath, err)
			continue
		}
		totalUpdated += updated
		fmt.Printf("%s entries=%d shelf_entries=%d candidate_books=%d usable=%d updated=%d\n",
			rel(catalogPath), stats.entries, stats.shelfEntries, stats.candidateBooks, stats.usableObservations, updated)
	}

	fmt.Printf("catalogs=%d entries=%d shelf_entries=%d candidate_books=%d usable=%d observations_added=%d\n",
		len(catalogs), totalStats.entries, totalStats.shelfEntries, totalStats.candidateBooks,
		totalStats.usableObservations, totalUpdated)
}

func findCatalogs(root string, pattern string) ([]string, error) {
	if pattern != "" {
		return filepath.Glob(pattern)
	}
	var out []string
	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			return nil
		}
		if filepath.Base(path) == "catalog.json" {
			out = append(out, path)
		}
		return nil
	})
	return out, err
}

type catalogStats struct {
	entries            int
	shelfEntries       int
	candidateBooks     int
	usableObservations int
}

func (s *catalogStats) add(other catalogStats) {
	s.entries += other.entries
	s.shelfEntries += other.shelfEntries
	s.candidateBooks += other.candidateBooks
	s.usableObservations += other.usableObservations
}

func summarizeCatalog(catalog map[string]any) catalogStats {
	var stats catalogStats
	entries, _ := catalog["entries"].([]any)
	stats.entries = len(entries)
	for _, rawEntry := range entries {
		entry, ok := rawEntry.(map[string]any)
		if !ok {
			continue
		}
		shelfID, _ := entry["shelf_id"].(string)
		if shelfID != "" {
			stats.shelfEntries++
		}
		books, _ := entry["books"].([]any)
		for _, rawBook := range books {
			book, ok := rawBook.(map[string]any)
			if !ok {
				continue
			}
			_, _, ok = topLibraryCandidate(book)
			if ok {
				stats.candidateBooks++
				if shelfID != "" {
					stats.usableObservations++
				}
			}
		}
	}
	return stats
}

func topLibraryCandidate(book map[string]any) (int, float64, bool) {
	lookup, _ := book["book_lookup"].(map[string]any)
	candidates, _ := lookup["candidates"].([]any)
	if len(candidates) == 0 {
		return 0, 0, false
	}
	candidate, _ := candidates[0].(map[string]any)
	id, ok := numberAsInt(candidate["library_db_id"])
	if !ok || id <= 0 {
		return 0, 0, false
	}
	score, ok := numberAsFloat(candidate["score"])
	if !ok {
		score = 0.65
	}
	return id, score, true
}

func numberAsInt(v any) (int, bool) {
	switch n := v.(type) {
	case int:
		return n, true
	case int64:
		return int(n), true
	case float64:
		return int(n), true
	default:
		return 0, false
	}
}

func numberAsFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	default:
		return 0, false
	}
}

func readCatalog(path string) (map[string]any, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var catalog map[string]any
	if err := json.Unmarshal(raw, &catalog); err != nil {
		return nil, err
	}
	return catalog, nil
}

func rel(path string) string {
	cwd, err := os.Getwd()
	if err != nil {
		return path
	}
	r, err := filepath.Rel(cwd, path)
	if err != nil || strings.HasPrefix(r, "..") {
		return path
	}
	return r
}
