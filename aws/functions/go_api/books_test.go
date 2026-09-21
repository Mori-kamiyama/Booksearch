package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

type fakeFeaturedPersistence struct {
	manifests map[string]featuredManifest
	latest    featuredManifest
	failSave  bool
}

func (p *fakeFeaturedPersistence) loadWeek(_ context.Context, week string) (featuredManifest, error) {
	manifest, ok := p.manifests[week]
	if !ok {
		return featuredManifest{}, errors.New("missing manifest")
	}
	return manifest, nil
}

func (p *fakeFeaturedPersistence) loadLatest(_ context.Context) (featuredManifest, error) {
	if p.latest.Week == "" {
		return featuredManifest{}, errors.New("missing latest")
	}
	return p.latest, nil
}

func (p *fakeFeaturedPersistence) save(_ context.Context, manifest featuredManifest) (featuredManifest, error) {
	if p.failSave {
		return featuredManifest{}, errors.New("persistence unavailable")
	}
	if p.manifests == nil {
		p.manifests = make(map[string]featuredManifest)
	}
	if existing, ok := p.manifests[manifest.Week]; ok {
		manifest = existing
	} else {
		p.manifests[manifest.Week] = manifest
	}
	p.latest = manifest
	return manifest, nil
}

type mockS3Object struct {
	data []byte
	etag string
}

type mockS3Server struct {
	mu      sync.Mutex
	objects map[string]mockS3Object
	nextTag int
	ifMatch int
	ifNone  int
}

func (m *mockS3Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	key := strings.TrimPrefix(r.URL.Path, "/")
	if parts := strings.SplitN(key, "/", 2); len(parts) == 2 {
		key = parts[1]
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	switch r.Method {
	case http.MethodGet:
		object, ok := m.objects[key]
		if !ok {
			w.Header().Set("x-amz-error-code", "NoSuchKey")
			w.WriteHeader(http.StatusNotFound)
			_, _ = io.WriteString(w, `<Error><Code>NoSuchKey</Code><Message>missing</Message></Error>`)
			return
		}
		w.Header().Set("ETag", object.etag)
		_, _ = w.Write(object.data)
	case http.MethodPut:
		object, exists := m.objects[key]
		if value := r.Header.Get("If-None-Match"); value == "*" {
			m.ifNone++
			if exists {
				w.Header().Set("x-amz-error-code", "PreconditionFailed")
				w.WriteHeader(http.StatusPreconditionFailed)
				_, _ = io.WriteString(w, `<Error><Code>PreconditionFailed</Code><Message>lost race</Message></Error>`)
				return
			}
		}
		if value := r.Header.Get("If-Match"); value != "" {
			m.ifMatch++
			if !exists || value != object.etag {
				w.Header().Set("x-amz-error-code", "PreconditionFailed")
				w.WriteHeader(http.StatusPreconditionFailed)
				_, _ = io.WriteString(w, `<Error><Code>PreconditionFailed</Code><Message>lost race</Message></Error>`)
				return
			}
		}
		data, _ := io.ReadAll(r.Body)
		m.nextTag++
		m.objects[key] = mockS3Object{data: data, etag: fmt.Sprintf(`"etag-%d"`, m.nextTag)}
		w.WriteHeader(http.StatusOK)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

func TestFeaturedReturnsBooksWithCovers(t *testing.T) {
	store, err := OpenBookStore("library.db")
	if err != nil {
		t.Fatalf("open library DB: %v", err)
	}
	defer store.db.Close()

	books, err := store.Featured(5)
	if err != nil {
		t.Fatalf("featured books: %v", err)
	}
	if len(books) != 5 {
		t.Fatalf("got %d books, want 5", len(books))
	}
	for _, book := range books {
		if book.Thumbnail == nil || *book.Thumbnail == "" {
			t.Fatalf("book %d (%s) has no cover", book.ID, book.Title)
		}
	}
}

func TestFeaturedIsStableWithinWeek(t *testing.T) {
	store, err := OpenBookStore("library.db")
	if err != nil {
		t.Fatalf("open library DB: %v", err)
	}
	defer store.db.Close()

	first, err := store.Featured(5)
	if err != nil {
		t.Fatalf("first featured books: %v", err)
	}
	if err := store.db.Close(); err != nil {
		t.Fatalf("close DB after cache fill: %v", err)
	}
	second, err := store.Featured(5)
	if err != nil {
		t.Fatalf("second featured books: %v", err)
	}
	if len(first) != len(second) {
		t.Fatalf("featured result lengths differ: %d vs %d", len(first), len(second))
	}
	for i := range first {
		if first[i].ID != second[i].ID {
			t.Fatalf("featured order changed at %d: %d vs %d", i, first[i].ID, second[i].ID)
		}
	}
}

func TestFeaturedPersistsAcrossRestartAndKeepsPreviousOnFailure(t *testing.T) {
	oldNow := featuredNow
	defer func() { featuredNow = oldNow }()
	featuredNow = func() time.Time { return time.Date(2026, 9, 21, 0, 0, 0, 0, time.UTC) }
	persistence := &fakeFeaturedPersistence{manifests: make(map[string]featuredManifest)}

	store, err := OpenBookStore("library.db")
	if err != nil {
		t.Fatalf("open library DB: %v", err)
	}
	store.featuredPersist = persistence
	first, err := store.Featured(5)
	if err != nil || len(first) != 5 {
		t.Fatalf("first featured = %d, err=%v", len(first), err)
	}
	for _, book := range first {
		if book.ID <= 0 {
			t.Fatalf("featured contains invalid ID: %+v", book)
		}
	}
	_ = store.db.Close()

	restarted, err := OpenBookStore("library.db")
	if err != nil {
		t.Fatalf("reopen library DB: %v", err)
	}
	defer restarted.db.Close()
	restarted.featuredPersist = persistence
	second, err := restarted.Featured(5)
	if err != nil {
		t.Fatalf("restart featured: %v", err)
	}
	for i := range first {
		if second[i].ID != first[i].ID {
			t.Fatalf("restart changed order at %d: %d vs %d", i, second[i].ID, first[i].ID)
		}
	}

	featuredNow = func() time.Time { return time.Date(2026, 9, 28, 0, 0, 0, 0, time.UTC) }
	persistence.failSave = true
	previous := append([]Book(nil), second...)
	got, err := restarted.Featured(5)
	if err != nil {
		t.Fatalf("failed week update: %v", err)
	}
	for i := range previous {
		if got[i].ID != previous[i].ID {
			t.Fatalf("failed week update changed order at %d: %d vs %d", i, got[i].ID, previous[i].ID)
		}
	}
}

func TestS3FeaturedPersistenceUsesImmutableManifestAndLatestCAS(t *testing.T) {
	state := &mockS3Server{objects: make(map[string]mockS3Object)}
	server := httptest.NewServer(state)
	defer server.Close()
	cfg := aws.Config{
		Region:      "us-east-1",
		Credentials: credentials.NewStaticCredentialsProvider("test", "test", ""),
	}
	client := s3.NewFromConfig(cfg, func(options *s3.Options) {
		options.BaseEndpoint = aws.String(server.URL)
		options.UsePathStyle = true
	})
	persistence := &s3FeaturedPersistence{client: client, bucket: "books"}
	book := Book{ID: 42, Title: "real book"}
	first, err := persistence.save(context.Background(), featuredManifest{Week: "2026-39", Books: []Book{book}})
	if err != nil || first.Week != "2026-39" {
		t.Fatalf("first S3 save = %+v, err=%v", first, err)
	}
	second, err := persistence.save(context.Background(), featuredManifest{Week: "2026-40", Books: []Book{book}})
	if err != nil || second.Week != "2026-40" {
		t.Fatalf("second S3 save = %+v, err=%v", second, err)
	}
	// A delayed writer for the previous week must read the newer winner rather
	// than replace latest.json.
	winner, err := persistence.save(context.Background(), featuredManifest{Week: "2026-39", Books: []Book{book}})
	if err != nil || winner.Week != "2026-40" {
		t.Fatalf("delayed save winner = %+v, err=%v", winner, err)
	}
	state.mu.Lock()
	latest := string(state.objects[featuredLatestKey].data)
	manifest := string(state.objects[featuredManifestPrefix+"2026-39.json"].data)
	if state.ifNone == 0 || state.ifMatch == 0 {
		t.Fatalf("CAS headers were not exercised: if-none=%d if-match=%d", state.ifNone, state.ifMatch)
	}
	state.mu.Unlock()
	if !strings.Contains(latest, `"week":"2026-40"`) {
		t.Fatalf("latest pointer rolled back: %s", latest)
	}
	if !strings.Contains(manifest, `"id":42`) {
		t.Fatalf("manifest lost real book ID: %s", manifest)
	}
}

func TestFeaturedPersistenceFailureDoesNotPinWeekInMemory(t *testing.T) {
	oldNow := featuredNow
	defer func() { featuredNow = oldNow }()
	featuredNow = func() time.Time { return time.Date(2026, 9, 21, 0, 0, 0, 0, time.UTC) }
	persistence := &fakeFeaturedPersistence{manifests: make(map[string]featuredManifest), failSave: true}
	store, err := OpenBookStore("library.db")
	if err != nil {
		t.Fatal(err)
	}
	defer store.db.Close()
	store.featuredPersist = persistence
	first, err := store.Featured(5)
	if err != nil || len(first) != 5 {
		t.Fatalf("failed persistence response = %d, err=%v", len(first), err)
	}
	persistence.failSave = false
	if _, err := store.Featured(5); err != nil {
		t.Fatal(err)
	}
	if store.featuredWeek == featuredWeekKey() {
		t.Fatal("backoff request pinned an unsaved week")
	}
	time.Sleep(featuredPersistenceBackoff + 10*time.Millisecond)
	second, err := store.Featured(5)
	if err != nil || len(second) != 5 {
		t.Fatalf("retry persistence response = %d, err=%v", len(second), err)
	}
	if _, ok := persistence.manifests[featuredWeekKey()]; !ok {
		t.Fatal("second request did not retry the failed week persistence")
	}
}

func TestRefreshFeaturedRequiresPersistedNonEmptyCurrentWeek(t *testing.T) {
	oldNow := featuredNow
	defer func() { featuredNow = oldNow }()
	featuredNow = func() time.Time { return time.Date(2026, 9, 21, 0, 0, 0, 0, time.UTC) }

	withoutPersistence, err := OpenBookStore("library.db")
	if err != nil {
		t.Fatal(err)
	}
	if err := withoutPersistence.RefreshFeatured(); err == nil {
		t.Fatal("RefreshFeatured should reject a store without persistence")
	}
	_ = withoutPersistence.db.Close()

	persistence := &fakeFeaturedPersistence{manifests: make(map[string]featuredManifest), failSave: true}
	store, err := OpenBookStore("library.db")
	if err != nil {
		t.Fatal(err)
	}
	defer store.db.Close()
	store.featuredPersist = persistence
	if err := store.RefreshFeatured(); err == nil {
		t.Fatal("RefreshFeatured should reject an unsaved generated result")
	}
	if store.featuredWeek == featuredWeekKey() {
		t.Fatal("failed refresh pinned the current week in memory")
	}
}

func TestFeaturedDoesNotServeDeletedIDsFromImmutableManifest(t *testing.T) {
	oldNow := featuredNow
	defer func() { featuredNow = oldNow }()
	featuredNow = func() time.Time { return time.Date(2026, 9, 21, 0, 0, 0, 0, time.UTC) }
	week := featuredWeekKey()
	persistence := &fakeFeaturedPersistence{
		manifests: map[string]featuredManifest{
			week: {Week: week, Books: []Book{{ID: 999999, Title: "deleted"}}},
		},
	}
	persistence.latest = persistence.manifests[week]
	store, err := OpenBookStore("library.db")
	if err != nil {
		t.Fatal(err)
	}
	defer store.db.Close()
	store.featuredPersist = persistence
	books, err := store.Featured(5)
	if err != nil {
		t.Fatal(err)
	}
	if len(books) != 0 {
		t.Fatalf("deleted immutable IDs were served: %+v", books)
	}
}

func TestSearchEscapesLikeWildcards(t *testing.T) {
	store, err := OpenBookStore("library.db")
	if err != nil {
		t.Fatalf("open library DB: %v", err)
	}
	defer store.db.Close()

	result, err := store.SearchWithTotal("%", 5)
	if err != nil {
		t.Fatalf("search literal wildcard: %v", err)
	}
	if result.Total != 0 || len(result.Books) != 0 {
		t.Fatalf("wildcard-only search matched books: total=%d books=%d", result.Total, len(result.Books))
	}
}

func TestIndexEntriesIncludeTitles(t *testing.T) {
	store, err := OpenBookStore("library.db")
	if err != nil {
		t.Fatalf("open library DB: %v", err)
	}
	defer store.db.Close()

	entries, err := store.IndexEntries([]int{1, 1, 2})
	if err != nil {
		t.Fatalf("index entries: %v", err)
	}
	if len(entries) == 0 {
		t.Fatal("expected index entries")
	}
	for id, entry := range entries {
		if entry.Title == "" {
			t.Fatalf("book %d has no title", id)
		}
		if entry.TitleReading == "" {
			t.Fatalf("book %d has no title reading", id)
		}
	}
}

func TestTitleReadingUsesMorphologicalAnalysis(t *testing.T) {
	tests := map[string]string{
		"老人と海":     "ロウジントウミ",
		"自然言語処理":   "シゼンゲンゴショリ",
		"Apple入門":  "Appleニュウモン",
		"『図書館の本』":  "トショカンノホン",
		"耐量子計算機暗号": "タイリョウシケイサンキアンゴウ",
		"3ステップで学ぶ": "サンステップデマナブ",
	}
	for title, want := range tests {
		if got := titleReading(title); got != want {
			t.Errorf("titleReading(%q) = %q, want %q", title, got, want)
		}
	}
}

func TestAllIndexBooksIncludesEntireCatalog(t *testing.T) {
	store, err := OpenBookStore("library.db")
	if err != nil {
		t.Fatalf("open library DB: %v", err)
	}
	defer store.db.Close()

	books, err := store.AllIndexBooks()
	if err != nil {
		t.Fatalf("all index books: %v", err)
	}
	if len(books) != 4202 {
		t.Fatalf("got %d index books, want 4202", len(books))
	}
	var unsupported []string
	for _, book := range books {
		if book.ID <= 0 || book.Title == "" || book.TitleReading == "" {
			t.Fatalf("incomplete index book: %+v", book)
		}
		first, _ := utf8.DecodeRuneInString(strings.TrimSpace(book.TitleReading))
		if !((first >= 'ぁ' && first <= 'ゖ') || (first >= 'ァ' && first <= 'ヶ') || (first >= 'A' && first <= 'Z') || (first >= 'a' && first <= 'z')) {
			unsupported = append(unsupported, fmt.Sprintf("%d:%s => %s", book.ID, book.Title, book.TitleReading))
		}
	}
	if len(unsupported) > 0 {
		limit := min(len(unsupported), 30)
		t.Fatalf("%d books would fall into the catch-all group:\n%s", len(unsupported), strings.Join(unsupported[:limit], "\n"))
	}
}
