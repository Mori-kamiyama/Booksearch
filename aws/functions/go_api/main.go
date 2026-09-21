package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/aws/aws-lambda-go/events"
	"github.com/aws/aws-lambda-go/lambda"
	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/feature/dynamodb/attributevalue"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	ddbtypes "github.com/aws/aws-sdk-go-v2/service/dynamodb/types"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/sqs"
	"github.com/google/uuid"
)

var (
	bucket               string
	jobsTable            string
	shelfCandidatesTable string
	yoloQueueURL         string
	lookupQueueURL       string
	s3Client             *s3.Client
	ddbClient            *dynamodb.Client
	sqsClient            *sqs.Client
	bookStore            *BookStore
	staticAssets         string
	googleBooksClient    = &http.Client{Timeout: 4 * time.Second}
)

func init() {
	bucket = os.Getenv("BUCKET")
	jobsTable = os.Getenv("JOBS_TABLE")
	shelfCandidatesTable = os.Getenv("SHELF_CANDIDATES_TABLE")
	yoloQueueURL = os.Getenv("YOLO_QUEUE_URL")
	lookupQueueURL = os.Getenv("LOOKUP_QUEUE_URL")
	staticAssets = os.Getenv("LAMBDA_TASK_ROOT")

	cfg, err := awsconfig.LoadDefaultConfig(context.Background())
	if err != nil {
		log.Fatalf("aws config: %v", err)
	}
	s3Client = s3.NewFromConfig(cfg)
	ddbClient = dynamodb.NewFromConfig(cfg)
	sqsClient = sqs.NewFromConfig(cfg)

	dbPath := filepath.Join(staticAssets, "library.db")
	if _, statErr := os.Stat(dbPath); statErr != nil {
		log.Printf("warn: library.db stat failed at %s: %v", dbPath, statErr)
	}
	bookStore, err = OpenBookStore(dbPath)
	if err != nil {
		log.Printf("warn: library.db not available (path=%s): %v", dbPath, err)
	}
}

func main() {
	lambda.Start(handler)
}

func handler(ctx context.Context, raw json.RawMessage) (events.APIGatewayV2HTTPResponse, error) {
	req, method, path := parseRequest(raw)

	if method == "OPTIONS" {
		return okJSON(204, nil), nil
	}

	switch {
	case method == "GET" && path == "/api/health":
		return okJSON(200, map[string]any{"status": "ok", "time": time.Now().Unix()}), nil
	case method == "GET" && path == "/api/books/search":
		return searchBooks(ctx, req)
	case method == "GET" && path == "/api/books/featured":
		return featuredBooks(ctx, req)
	case method == "GET" && path == "/api/books/index":
		return indexBooks()
	case method == "GET" && path == "/api/shelf-candidates":
		return listShelfCandidates(ctx)
	case method == "GET" && strings.HasPrefix(path, "/api/books/"):
		return getBook(ctx, req)
	case method == "GET" && strings.HasPrefix(path, "/api/jobs/"):
		return getJob(ctx, req)
	case method == "POST" && path == "/api/scan":
		return scan(ctx, req)
	case method == "POST" && path == "/api/scan/init":
		return scanInit(ctx, req)
	case method == "POST" && path == "/api/scan/start":
		return scanStart(ctx, req)
	case method == "POST" && path == "/api/scan/sessions":
		return liveSessionStart(ctx)
	case method == "POST" && strings.HasPrefix(path, "/api/scan/sessions/") && strings.HasSuffix(path, "/frames"):
		return liveSessionFrame(ctx, req, path)
	case method == "POST" && strings.HasPrefix(path, "/api/scan/sessions/") && strings.HasSuffix(path, "/commit-frame"):
		return liveSessionFrameCommit(ctx, req, path)
	case method == "POST" && strings.HasPrefix(path, "/api/scan/sessions/") && strings.HasSuffix(path, "/complete"):
		return liveSessionComplete(ctx, path)
	case method == "POST" && strings.HasPrefix(path, "/api/scan/sessions/") && strings.HasSuffix(path, "/cancel"):
		return liveSessionCancel(ctx, path)
	case method == "GET" && path == "/api/shelves":
		return getShelves(ctx)
	case method == "GET" && strings.HasPrefix(path, "/api/crops/"):
		return getCrop(ctx, path)
	default:
		return errJSON(404, "not found"), nil
	}
}

func indexBooks() (events.APIGatewayV2HTTPResponse, error) {
	if bookStore == nil {
		return errJSON(503, "library database unavailable"), nil
	}
	books, err := bookStore.AllIndexBooks()
	if err != nil {
		return errJSON(500, err.Error()), nil
	}
	return okJSON(200, map[string]any{"books": books}), nil
}

// Live sessions process each committed frame while the camera is still running.
// Completion only closes the session and waits for the already queued work.
func liveSessionStart(ctx context.Context) (events.APIGatewayV2HTTPResponse, error) {
	id := uuid.New().String()
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := ddbClient.PutItem(ctx, &dynamodb.PutItemInput{TableName: aws.String(jobsTable), Item: map[string]ddbtypes.AttributeValue{
		"job_id": &ddbtypes.AttributeValueMemberS{Value: id}, "status": &ddbtypes.AttributeValueMemberS{Value: "collecting"},
		"session_type": &ddbtypes.AttributeValueMemberS{Value: "live"}, "frame_keys": &ddbtypes.AttributeValueMemberL{Value: []ddbtypes.AttributeValue{}},
		"accepted_frames": &ddbtypes.AttributeValueMemberN{Value: "0"}, "processed_frames": &ddbtypes.AttributeValueMemberN{Value: "0"},
		"crop_total": &ddbtypes.AttributeValueMemberN{Value: "0"}, "ocr_total": &ddbtypes.AttributeValueMemberN{Value: "0"}, "ocr_done": &ddbtypes.AttributeValueMemberN{Value: "0"},
		"created_at": &ddbtypes.AttributeValueMemberS{Value: now}, "updated_at": &ddbtypes.AttributeValueMemberS{Value: now},
	}})
	if err != nil {
		return errJSON(500, "ddb session: "+err.Error()), nil
	}
	return okJSON(201, map[string]any{"session_id": id, "frame_upload_url_endpoint": "/api/scan/sessions/" + id + "/frames", "content_type": "image/jpeg"}), nil
}

func liveSessionID(path, suffix string) string {
	return strings.TrimSuffix(strings.TrimPrefix(path, "/api/scan/sessions/"), suffix)
}
func liveSessionFrame(ctx context.Context, req events.APIGatewayV2HTTPRequest, path string) (events.APIGatewayV2HTTPResponse, error) {
	id := liveSessionID(path, "/frames")
	var payload struct {
		Filename string `json:"filename"`
	}
	if err := json.Unmarshal([]byte(decodeBody(req)), &payload); err != nil || payload.Filename == "" {
		return errJSON(400, "expected JSON {filename}"), nil
	}
	key := fmt.Sprintf("live/%s/frames/%s.jpg", id, uuid.New().String())
	presigner := s3.NewPresignClient(s3Client)
	p, err := presigner.PresignPutObject(ctx, &s3.PutObjectInput{Bucket: aws.String(bucket), Key: aws.String(key), ContentType: aws.String("image/jpeg")}, func(o *s3.PresignOptions) { o.Expires = 15 * time.Minute })
	if err != nil {
		return errJSON(500, "presign: "+err.Error()), nil
	}
	_, err = ddbClient.UpdateItem(ctx, &dynamodb.UpdateItemInput{
		TableName: aws.String(jobsTable), Key: map[string]ddbtypes.AttributeValue{"job_id": &ddbtypes.AttributeValueMemberS{Value: id}},
		UpdateExpression:    aws.String("SET frame_keys = list_append(frame_keys, :k), updated_at = :u"),
		ConditionExpression: aws.String("#s = :collecting"), ExpressionAttributeNames: map[string]string{"#s": "status"},
		ExpressionAttributeValues: map[string]ddbtypes.AttributeValue{
			":k": &ddbtypes.AttributeValueMemberL{Value: []ddbtypes.AttributeValue{&ddbtypes.AttributeValueMemberS{Value: key}}},
			":u": &ddbtypes.AttributeValueMemberS{Value: time.Now().UTC().Format(time.RFC3339)}, ":collecting": &ddbtypes.AttributeValueMemberS{Value: "collecting"},
		},
	})
	if err != nil {
		return errJSON(500, "ddb frame: "+err.Error()), nil
	}
	return okJSON(200, map[string]any{"frame_id": filepath.Base(key), "frame_key": key, "upload_url": p.URL, "content_type": "image/jpeg"}), nil
}

func liveSessionFrameCommit(ctx context.Context, req events.APIGatewayV2HTTPRequest, path string) (events.APIGatewayV2HTTPResponse, error) {
	id := liveSessionID(path, "/commit-frame")
	var payload struct {
		FrameKey string `json:"frame_key"`
	}
	if err := json.Unmarshal([]byte(decodeBody(req)), &payload); err != nil || payload.FrameKey == "" {
		return errJSON(400, "expected JSON {frame_key}"), nil
	}
	prefix := "live/" + id + "/frames/"
	if !strings.HasPrefix(payload.FrameKey, prefix) || strings.Contains(strings.TrimPrefix(payload.FrameKey, prefix), "/") {
		return errJSON(400, "invalid frame_key"), nil
	}
	if _, err := s3Client.HeadObject(ctx, &s3.HeadObjectInput{Bucket: aws.String(bucket), Key: aws.String(payload.FrameKey)}); err != nil {
		return errJSON(409, "frame upload is not visible yet"), nil
	}

	keySet := &ddbtypes.AttributeValueMemberSS{Value: []string{payload.FrameKey}}
	_, err := ddbClient.UpdateItem(ctx, &dynamodb.UpdateItemInput{
		TableName: aws.String(jobsTable), Key: map[string]ddbtypes.AttributeValue{"job_id": &ddbtypes.AttributeValueMemberS{Value: id}},
		UpdateExpression:         aws.String("SET updated_at = :u ADD committed_frame_keys :keys, accepted_frames :one"),
		ConditionExpression:      aws.String("#s = :collecting AND (attribute_not_exists(committed_frame_keys) OR NOT contains(committed_frame_keys, :key))"),
		ExpressionAttributeNames: map[string]string{"#s": "status"},
		ExpressionAttributeValues: map[string]ddbtypes.AttributeValue{
			":keys": keySet, ":key": &ddbtypes.AttributeValueMemberS{Value: payload.FrameKey},
			":one": &ddbtypes.AttributeValueMemberN{Value: "1"}, ":collecting": &ddbtypes.AttributeValueMemberS{Value: "collecting"},
			":u": &ddbtypes.AttributeValueMemberS{Value: time.Now().UTC().Format(time.RFC3339)},
		},
	})
	if err != nil {
		if strings.Contains(err.Error(), "ConditionalCheckFailedException") {
			return okJSON(200, map[string]any{"session_id": id, "frame_key": payload.FrameKey, "status": "already_committed"}), nil
		}
		return errJSON(500, "commit frame: "+err.Error()), nil
	}

	msg, _ := json.Marshal(map[string]any{"job_id": id, "image_keys": []string{payload.FrameKey}, "incremental": true})
	if _, err := sqsClient.SendMessage(ctx, &sqs.SendMessageInput{QueueUrl: aws.String(yoloQueueURL), MessageBody: aws.String(string(msg))}); err != nil {
		_, _ = ddbClient.UpdateItem(ctx, &dynamodb.UpdateItemInput{
			TableName: aws.String(jobsTable), Key: map[string]ddbtypes.AttributeValue{"job_id": &ddbtypes.AttributeValueMemberS{Value: id}},
			UpdateExpression:          aws.String("ADD accepted_frames :minusOne DELETE committed_frame_keys :keys"),
			ExpressionAttributeValues: map[string]ddbtypes.AttributeValue{":keys": keySet, ":minusOne": &ddbtypes.AttributeValueMemberN{Value: "-1"}},
		})
		return errJSON(500, "queue frame: "+err.Error()), nil
	}
	return okJSON(202, map[string]any{"session_id": id, "frame_key": payload.FrameKey, "status": "processing"}), nil
}
func liveSessionComplete(ctx context.Context, path string) (events.APIGatewayV2HTTPResponse, error) {
	id := liveSessionID(path, "/complete")
	out, err := ddbClient.GetItem(ctx, &dynamodb.GetItemInput{TableName: aws.String(jobsTable), Key: map[string]ddbtypes.AttributeValue{"job_id": &ddbtypes.AttributeValueMemberS{Value: id}}, ConsistentRead: aws.Bool(true)})
	if err != nil {
		return errJSON(500, "session lookup: "+err.Error()), nil
	}
	if out.Item == nil {
		return errJSON(404, "session not found"), nil
	}
	status, _ := out.Item["status"].(*ddbtypes.AttributeValueMemberS)
	if status != nil && status.Value != "collecting" {
		// Completion is retryable after a transient final-lookup enqueue failure.
		// Reuse the current counters and queue the lookup once the workers have
		// finished; completed/queued sessions are already idempotently complete.
		switch status.Value {
		case "processing":
			if liveWorkFinished(out.Item) {
				if err := queueFinalLookup(ctx, id); err != nil {
					return errJSON(500, err.Error()), nil
				}
			}
		case "lookup_pending", "done", "no_detection", "no_readable_crops":
			// These states are already closed or have their final lookup queued.
		case "canceled":
			return errJSON(409, "session canceled"), nil
		default:
			return errJSON(409, "session is not completable"), nil
		}
		return okJSON(202, map[string]any{"job_id": id, "status": status.Value}), nil
	}
	committed, _ := out.Item["committed_frame_keys"].(*ddbtypes.AttributeValueMemberSS)
	if committed == nil || len(committed.Value) == 0 {
		return errJSON(400, "no accepted frames"), nil
	}
	// The counters must be re-read from the write that sets scan_closed. A worker
	// that finished its last frame just before this update saw scan_closed=false
	// and skipped the final lookup, so the stale pre-update snapshot would leave
	// the job stuck in processing forever.
	closed, err := ddbClient.UpdateItem(ctx, &dynamodb.UpdateItemInput{
		TableName: aws.String(jobsTable), Key: map[string]ddbtypes.AttributeValue{"job_id": &ddbtypes.AttributeValueMemberS{Value: id}},
		UpdateExpression: aws.String("SET #s = :s, scan_closed = :closed, updated_at = :u"), ConditionExpression: aws.String("#s = :collecting"),
		ExpressionAttributeNames: map[string]string{"#s": "status"}, ExpressionAttributeValues: map[string]ddbtypes.AttributeValue{
			":s": &ddbtypes.AttributeValueMemberS{Value: "processing"}, ":collecting": &ddbtypes.AttributeValueMemberS{Value: "collecting"},
			":closed": &ddbtypes.AttributeValueMemberBOOL{Value: true}, ":u": &ddbtypes.AttributeValueMemberS{Value: time.Now().UTC().Format(time.RFC3339)},
		},
		ReturnValues: ddbtypes.ReturnValueAllNew,
	})
	if err != nil {
		if strings.Contains(err.Error(), "ConditionalCheckFailedException") {
			return errJSON(409, "session state changed; retry completion"), nil
		}
		return errJSON(500, err.Error()), nil
	}
	if liveWorkFinished(closed.Attributes) {
		if err := queueFinalLookup(ctx, id); err != nil {
			return errJSON(500, err.Error()), nil
		}
	}
	return okJSON(202, map[string]any{"job_id": id, "accepted_frames": len(committed.Value)}), nil
}

func numberAttribute(item map[string]ddbtypes.AttributeValue, key string) int {
	if n, ok := item[key].(*ddbtypes.AttributeValueMemberN); ok {
		value, _ := strconv.Atoi(n.Value)
		return value
	}
	return 0
}

func liveWorkFinished(item map[string]ddbtypes.AttributeValue) bool {
	return numberAttribute(item, "processed_frames") >= numberAttribute(item, "accepted_frames") &&
		numberAttribute(item, "ocr_done") >= numberAttribute(item, "ocr_total")
}

func queueFinalLookup(ctx context.Context, id string) error {
	_, err := ddbClient.UpdateItem(ctx, &dynamodb.UpdateItemInput{
		TableName: aws.String(jobsTable), Key: map[string]ddbtypes.AttributeValue{"job_id": &ddbtypes.AttributeValueMemberS{Value: id}},
		UpdateExpression:         aws.String("SET final_lookup_queued = :yes, #s = :pending"),
		ConditionExpression:      aws.String("attribute_not_exists(final_lookup_queued)"),
		ExpressionAttributeNames: map[string]string{"#s": "status"},
		ExpressionAttributeValues: map[string]ddbtypes.AttributeValue{
			":yes": &ddbtypes.AttributeValueMemberBOOL{Value: true}, ":pending": &ddbtypes.AttributeValueMemberS{Value: "lookup_pending"},
		},
	})
	if err != nil {
		if strings.Contains(err.Error(), "ConditionalCheckFailedException") {
			return nil
		}
		return fmt.Errorf("finalize live session: %w", err)
	}
	msg, _ := json.Marshal(map[string]any{"job_id": id, "incremental": false})
	if _, err := sqsClient.SendMessage(ctx, &sqs.SendMessageInput{QueueUrl: aws.String(lookupQueueURL), MessageBody: aws.String(string(msg))}); err != nil {
		_, _ = ddbClient.UpdateItem(ctx, &dynamodb.UpdateItemInput{
			TableName: aws.String(jobsTable), Key: map[string]ddbtypes.AttributeValue{"job_id": &ddbtypes.AttributeValueMemberS{Value: id}},
			UpdateExpression:          aws.String("SET #s = :processing REMOVE final_lookup_queued"),
			ExpressionAttributeNames:  map[string]string{"#s": "status"},
			ExpressionAttributeValues: map[string]ddbtypes.AttributeValue{":processing": &ddbtypes.AttributeValueMemberS{Value: "processing"}},
		})
		return fmt.Errorf("queue final lookup: %w", err)
	}
	return nil
}
func liveSessionCancel(ctx context.Context, path string) (events.APIGatewayV2HTTPResponse, error) {
	id := liveSessionID(path, "/cancel")
	out, getErr := ddbClient.GetItem(ctx, &dynamodb.GetItemInput{TableName: aws.String(jobsTable), Key: map[string]ddbtypes.AttributeValue{"job_id": &ddbtypes.AttributeValueMemberS{Value: id}}, ConsistentRead: aws.Bool(true)})
	if getErr != nil {
		return errJSON(500, "session lookup: "+getErr.Error()), nil
	}
	if out.Item == nil {
		return errJSON(404, "session not found"), nil
	}
	status, _ := out.Item["status"].(*ddbtypes.AttributeValueMemberS)
	if status != nil && status.Value != "collecting" {
		if status.Value == "canceled" {
			return okJSON(200, map[string]any{"session_id": id, "status": "canceled"}), nil
		}
		return errJSON(409, "session already closed"), nil
	}
	updated, err := ddbClient.UpdateItem(ctx, &dynamodb.UpdateItemInput{TableName: aws.String(jobsTable), Key: map[string]ddbtypes.AttributeValue{"job_id": &ddbtypes.AttributeValueMemberS{Value: id}}, UpdateExpression: aws.String("SET #s = :s"), ConditionExpression: aws.String("#s = :collecting"), ExpressionAttributeNames: map[string]string{"#s": "status"}, ExpressionAttributeValues: map[string]ddbtypes.AttributeValue{":s": &ddbtypes.AttributeValueMemberS{Value: "canceled"}, ":collecting": &ddbtypes.AttributeValueMemberS{Value: "collecting"}}, ReturnValues: ddbtypes.ReturnValueAllNew})
	if err != nil {
		if strings.Contains(err.Error(), "ConditionalCheckFailedException") {
			return okJSON(200, map[string]any{"session_id": id, "status": "already_closed"}), nil
		}
		return errJSON(500, err.Error()), nil
	}
	if frames, ok := updated.Attributes["frame_keys"].(*ddbtypes.AttributeValueMemberL); ok {
		for _, v := range frames.Value {
			if s, ok := v.(*ddbtypes.AttributeValueMemberS); ok {
				_, _ = s3Client.DeleteObject(ctx, &s3.DeleteObjectInput{Bucket: aws.String(bucket), Key: aws.String(s.Value)})
			}
		}
	}
	return okJSON(200, map[string]any{"session_id": id, "status": "canceled"}), nil
}

func parseRequest(raw json.RawMessage) (events.APIGatewayV2HTTPRequest, string, string) {
	var req events.APIGatewayV2HTTPRequest
	_ = json.Unmarshal(raw, &req)

	method := req.RequestContext.HTTP.Method
	path := requestPath(req)
	if method != "" && path != "" {
		return req, method, path
	}

	var generic map[string]any
	if err := json.Unmarshal(raw, &generic); err != nil {
		return req, method, path
	}
	if method == "" {
		if v, ok := generic["httpMethod"].(string); ok {
			method = v
		}
	}
	if path == "" {
		if v, ok := generic["rawPath"].(string); ok {
			path = v
		} else if v, ok := generic["path"].(string); ok {
			path = v
		}
	}
	if method == "" || path == "" {
		if rc, ok := generic["requestContext"].(map[string]any); ok {
			if httpCtx, ok := rc["http"].(map[string]any); ok {
				if method == "" {
					if v, ok := httpCtx["method"].(string); ok {
						method = v
					}
				}
				if path == "" {
					if v, ok := httpCtx["path"].(string); ok {
						path = v
					}
				}
			}
		}
	}
	if req.RawPath == "" {
		req.RawPath = path
	}
	if req.QueryStringParameters == nil {
		if qs, ok := generic["queryStringParameters"].(map[string]any); ok {
			req.QueryStringParameters = map[string]string{}
			for k, v := range qs {
				if s, ok := v.(string); ok {
					req.QueryStringParameters[k] = s
				}
			}
		}
	}
	if req.Body == "" {
		if v, ok := generic["body"].(string); ok {
			req.Body = v
		}
	}
	if v, ok := generic["isBase64Encoded"].(bool); ok {
		req.IsBase64Encoded = v
	}
	return req, method, path
}

// ---- /api/books/search ----
func searchBooks(ctx context.Context, req events.APIGatewayV2HTTPRequest) (events.APIGatewayV2HTTPResponse, error) {
	q := req.QueryStringParameters["q"]
	if strings.TrimSpace(q) == "" {
		return errJSON(400, "q is required"), nil
	}
	limit := 20
	if l := req.QueryStringParameters["limit"]; l != "" {
		if n, err := strconv.Atoi(l); err == nil && n > 0 {
			limit = n
		}
	}
	if bookStore == nil {
		return errJSON(503, "library DB not available"), nil
	}
	result, err := bookStore.SearchWithTotal(q, limit)
	if err != nil {
		return errJSON(500, err.Error()), nil
	}
	books := result.Books
	if books == nil {
		books = []Book{}
	}
	attachShelfCandidates(ctx, books)
	attachCoverCache(ctx, books)
	return okJSON(200, map[string]any{"books": books, "query": q, "total": result.Total}), nil
}

// ---- /api/books/featured ----
func featuredBooks(ctx context.Context, req events.APIGatewayV2HTTPRequest) (events.APIGatewayV2HTTPResponse, error) {
	limit := 5
	if value := req.QueryStringParameters["limit"]; value != "" {
		if parsed, err := strconv.Atoi(value); err == nil && parsed > 0 {
			limit = parsed
		}
	}
	if bookStore == nil {
		return errJSON(503, "library DB not available"), nil
	}
	books, err := bookStore.Featured(limit)
	if err != nil {
		return errJSON(500, err.Error()), nil
	}
	attachShelfCandidates(ctx, books)
	response := okJSON(200, map[string]any{"books": books})
	response.Headers["Cache-Control"] = "public, max-age=300, s-maxage=3600"
	return response, nil
}

// ---- /api/books/{id} ----
func getBook(ctx context.Context, req events.APIGatewayV2HTTPRequest) (events.APIGatewayV2HTTPResponse, error) {
	id, err := strconv.Atoi(filepath.Base(req.RawPath))
	if err != nil {
		return errJSON(400, "invalid id"), nil
	}
	if bookStore == nil {
		return errJSON(503, "library DB not available"), nil
	}
	book, err := bookStore.GetByID(id)
	if err != nil {
		return errJSON(500, err.Error()), nil
	}
	if book == nil {
		return errJSON(404, "not found"), nil
	}
	books := []Book{*book}
	attachShelfCandidates(ctx, books)
	attachCoverCache(ctx, books)
	*book = books[0]
	return okJSON(200, book), nil
}

func attachShelfCandidates(ctx context.Context, books []Book) {
	if shelfCandidatesTable == "" {
		return
	}
	for i := range books {
		candidates, err := fetchShelfCandidates(ctx, books[i].ID)
		if err != nil {
			log.Printf("shelf candidates book_id=%d: %v", books[i].ID, err)
			continue
		}
		books[i].ShelfCandidates = candidates
		for _, c := range candidates {
			books[i].ShelfIDs = append(books[i].ShelfIDs, c.ShelfID)
		}
	}
}

type coverCacheEntry struct {
	Thumbnail *string `json:"thumbnail,omitempty"`
	InfoLink  *string `json:"info_link,omitempty"`
	Error     string  `json:"error,omitempty"`
	FetchedAt string  `json:"fetched_at"`
	Query     string  `json:"query,omitempty"`
	Title     string  `json:"title,omitempty"`
}

type googleBooksResponse struct {
	Items []struct {
		ID         string `json:"id"`
		VolumeInfo struct {
			Title      string `json:"title"`
			InfoLink   string `json:"infoLink"`
			ImageLinks struct {
				Thumbnail      string `json:"thumbnail"`
				SmallThumbnail string `json:"smallThumbnail"`
			} `json:"imageLinks"`
		} `json:"volumeInfo"`
	} `json:"items"`
}

func attachCoverCache(ctx context.Context, books []Book) {
	if bucket == "" || s3Client == nil {
		return
	}
	cacheKey := os.Getenv("COVER_CACHE_KEY")
	if cacheKey == "" {
		cacheKey = "cache/google_book_covers.json"
	}
	cache := loadCoverCache(ctx, cacheKey)
	changed := false
	fetchLimit := coverFetchLimit()
	fetched := 0

	for i := range books {
		if books[i].Thumbnail != nil && *books[i].Thumbnail != "" {
			continue
		}
		id := strconv.Itoa(books[i].ID)
		if entry, ok := cache[id]; ok {
			if entry.Thumbnail != nil && *entry.Thumbnail != "" {
				books[i].Thumbnail = entry.Thumbnail
				books[i].InfoLink = entry.InfoLink
				continue
			}
			if !coverCacheRetryDue(entry) {
				continue
			}
		}
		if fetched >= fetchLimit {
			continue
		}
		entry := fetchGoogleBookCover(ctx, books[i])
		cache[id] = entry
		changed = true
		fetched++
		if entry.Thumbnail != nil && *entry.Thumbnail != "" {
			books[i].Thumbnail = entry.Thumbnail
			books[i].InfoLink = entry.InfoLink
		}
	}

	if changed {
		saveCoverCache(ctx, cacheKey, cache)
	}
}

func coverFetchLimit() int {
	limit := 5
	if raw := os.Getenv("COVER_FETCH_LIMIT"); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil && n >= 0 {
			limit = n
		}
	}
	return limit
}

func coverCacheRetryDue(entry coverCacheEntry) bool {
	retryAfter := 30 * 24 * time.Hour
	if raw := os.Getenv("COVER_RETRY_AFTER_DAYS"); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil {
			retryAfter = time.Duration(n) * 24 * time.Hour
		}
	}
	if transientGoogleBooksError(entry.Error) {
		retryAfter = time.Hour
	}
	if retryAfter <= 0 || entry.FetchedAt == "" {
		return retryAfter <= 0
	}
	fetchedAt, err := time.Parse(time.RFC3339, entry.FetchedAt)
	if err != nil {
		return true
	}
	return time.Since(fetchedAt) >= retryAfter
}

func transientGoogleBooksError(err string) bool {
	if strings.Contains(err, "google books status 429") {
		return true
	}
	for _, code := range []string{"500", "502", "503", "504"} {
		if strings.Contains(err, "google books status "+code) {
			return true
		}
	}
	return false
}

func loadCoverCache(ctx context.Context, key string) map[string]coverCacheEntry {
	cache := map[string]coverCacheEntry{}
	out, err := s3Client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return cache
	}
	defer out.Body.Close()
	body, err := io.ReadAll(out.Body)
	if err != nil {
		log.Printf("cover cache read: %v", err)
		return cache
	}
	if err := json.Unmarshal(body, &cache); err != nil {
		log.Printf("cover cache decode: %v", err)
	}
	return cache
}

func saveCoverCache(ctx context.Context, key string, cache map[string]coverCacheEntry) {
	body, err := json.MarshalIndent(cache, "", "  ")
	if err != nil {
		log.Printf("cover cache encode: %v", err)
		return
	}
	_, err = s3Client.PutObject(ctx, &s3.PutObjectInput{
		Bucket:      aws.String(bucket),
		Key:         aws.String(key),
		Body:        strings.NewReader(string(body)),
		ContentType: aws.String("application/json; charset=utf-8"),
	})
	if err != nil {
		log.Printf("cover cache save: %v", err)
	}
}

func fetchGoogleBookCover(ctx context.Context, book Book) coverCacheEntry {
	query := googleBooksQuery(book)
	entry := coverCacheEntry{
		FetchedAt: time.Now().UTC().Format(time.RFC3339),
		Query:     query,
		Title:     book.Title,
	}
	if query == "" {
		entry.Error = "missing query"
		return entry
	}
	reqURL := "https://www.googleapis.com/books/v1/volumes?" + query
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, reqURL, nil)
	if err != nil {
		entry.Error = err.Error()
		return entry
	}
	resp, err := googleBooksClient.Do(req)
	if err != nil {
		entry.Error = err.Error()
		return entry
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		entry.Error = fmt.Sprintf("google books status %d", resp.StatusCode)
		return entry
	}
	var decoded googleBooksResponse
	if err := json.NewDecoder(resp.Body).Decode(&decoded); err != nil {
		entry.Error = err.Error()
		return entry
	}
	for _, item := range decoded.Items {
		info := item.VolumeInfo
		thumbnail := info.ImageLinks.Thumbnail
		if thumbnail == "" {
			thumbnail = info.ImageLinks.SmallThumbnail
		}
		if thumbnail == "" {
			continue
		}
		thumbnail = strings.Replace(thumbnail, "http://", "https://", 1)
		entry.Thumbnail = &thumbnail
		if info.InfoLink != "" {
			entry.InfoLink = &info.InfoLink
		}
		if info.Title != "" {
			entry.Title = info.Title
		}
		return entry
	}
	entry.Error = "no thumbnail"
	return entry
}

func googleBooksQuery(book Book) string {
	values := url.Values{}
	if isbn := normalizedISBN(book.ISBN); isbn != "" {
		values.Set("q", "isbn:"+isbn)
	} else if strings.TrimSpace(book.Title) != "" {
		values.Set("q", "intitle:"+strings.TrimSpace(book.Title))
	} else {
		return ""
	}
	values.Set("maxResults", "3")
	values.Set("printType", "books")
	if key := os.Getenv("GOOGLE_BOOKS_API_KEY"); key != "" {
		values.Set("key", key)
	}
	return values.Encode()
}

func normalizedISBN(raw string) string {
	var b strings.Builder
	for _, r := range raw {
		if r >= '0' && r <= '9' {
			b.WriteRune(r)
		} else if r == 'x' || r == 'X' {
			b.WriteRune('X')
		}
	}
	isbn := b.String()
	if len(isbn) >= 10 {
		return isbn
	}
	return ""
}

func fetchShelfCandidates(ctx context.Context, bookID int) ([]ShelfCandidate, error) {
	candidates := make([]ShelfCandidate, 0)
	var startKey map[string]ddbtypes.AttributeValue
	for {
		out, err := ddbClient.Query(ctx, &dynamodb.QueryInput{
			TableName:              aws.String(shelfCandidatesTable),
			KeyConditionExpression: aws.String("book_id = :b"),
			ExpressionAttributeValues: map[string]ddbtypes.AttributeValue{
				":b": &ddbtypes.AttributeValueMemberN{Value: strconv.Itoa(bookID)},
			},
			ExclusiveStartKey: startKey,
		})
		if err != nil {
			return nil, err
		}
		for _, item := range out.Items {
			candidates = append(candidates, shelfCandidateFromItem(item))
		}
		if len(out.LastEvaluatedKey) == 0 {
			break
		}
		startKey = out.LastEvaluatedKey
	}
	return candidates, nil
}

func listShelfCandidates(ctx context.Context) (events.APIGatewayV2HTTPResponse, error) {
	if shelfCandidatesTable == "" {
		return okJSON(200, map[string]any{"candidates": []any{}}), nil
	}
	candidates := make([]ShelfCandidate, 0)
	var startKey map[string]ddbtypes.AttributeValue
	for {
		out, err := ddbClient.Scan(ctx, &dynamodb.ScanInput{
			TableName:         aws.String(shelfCandidatesTable),
			Limit:             aws.Int32(500),
			ExclusiveStartKey: startKey,
		})
		if err != nil {
			return errJSON(500, err.Error()), nil
		}
		for _, item := range out.Items {
			candidates = append(candidates, shelfCandidateFromItem(item))
		}
		if len(out.LastEvaluatedKey) == 0 {
			break
		}
		startKey = out.LastEvaluatedKey
	}
	sort.SliceStable(candidates, func(i, j int) bool {
		if candidates[i].ShelfID != candidates[j].ShelfID {
			return candidates[i].ShelfID < candidates[j].ShelfID
		}
		if candidates[i].Confidence != candidates[j].Confidence {
			return candidates[i].Confidence > candidates[j].Confidence
		}
		return candidates[i].BookID < candidates[j].BookID
	})
	if bookStore != nil {
		ids := make([]int, 0, len(candidates))
		for _, candidate := range candidates {
			ids = append(ids, candidate.BookID)
		}
		entries, lookupErr := bookStore.IndexEntries(ids)
		if lookupErr != nil {
			log.Printf("warn: shelf candidate cover lookup failed: %v", lookupErr)
		} else {
			for index := range candidates {
				if entry, ok := entries[candidates[index].BookID]; ok {
					candidates[index].Title = entry.Title
					candidates[index].TitleReading = entry.TitleReading
					candidates[index].Thumbnail = entry.Thumbnail
				}
			}
		}
	}
	return okJSON(200, map[string]any{"candidates": candidates}), nil
}

func shelfCandidateFromItem(item map[string]ddbtypes.AttributeValue) ShelfCandidate {
	var c ShelfCandidate
	if v, ok := item["book_id"].(*ddbtypes.AttributeValueMemberN); ok {
		c.BookID, _ = strconv.Atoi(v.Value)
	}
	if v, ok := item["shelf_id"].(*ddbtypes.AttributeValueMemberS); ok {
		c.ShelfID = v.Value
	}
	if v, ok := item["confidence"].(*ddbtypes.AttributeValueMemberN); ok {
		c.Confidence, _ = strconv.ParseFloat(v.Value, 64)
	}
	if v, ok := item["observations"].(*ddbtypes.AttributeValueMemberN); ok {
		c.Observations, _ = strconv.Atoi(v.Value)
	}
	if v, ok := item["avg_score"].(*ddbtypes.AttributeValueMemberN); ok {
		c.AvgScore, _ = strconv.ParseFloat(v.Value, 64)
	}
	if v, ok := item["title"].(*ddbtypes.AttributeValueMemberS); ok {
		c.Title = v.Value
	}
	if v, ok := item["updated_at"].(*ddbtypes.AttributeValueMemberS); ok {
		c.UpdatedAt = v.Value
	}
	return c
}

// ---- POST /api/scan ----
// multipart アップロードを Lambda で扱うのは重いので、JSON で
// { "filename": "x.jpg", "content_base64": "..." } を受ける。
// （後でフロントから presigned PUT に置き換えやすい）
func scan(ctx context.Context, req events.APIGatewayV2HTTPRequest) (events.APIGatewayV2HTTPResponse, error) {
	body := req.Body
	if req.IsBase64Encoded {
		dec, err := base64.StdEncoding.DecodeString(body)
		if err != nil {
			return errJSON(400, "invalid base64"), nil
		}
		body = string(dec)
	}

	var payload struct {
		Filename      string `json:"filename"`
		ContentBase64 string `json:"content_base64"`
	}
	if err := json.Unmarshal([]byte(body), &payload); err != nil {
		return errJSON(400, "expected JSON {filename, content_base64}"), nil
	}
	if payload.ContentBase64 == "" {
		return errJSON(400, "content_base64 is required"), nil
	}
	raw, err := base64.StdEncoding.DecodeString(payload.ContentBase64)
	if err != nil {
		return errJSON(400, "content_base64 decode failed"), nil
	}

	ext := strings.ToLower(filepath.Ext(payload.Filename))
	if ext == "" {
		ext = ".jpg"
	}
	jobID := uuid.New().String()
	key := fmt.Sprintf("uploads/%s/upload%s", jobID, ext)

	if _, err := s3Client.PutObject(ctx, &s3.PutObjectInput{
		Bucket:      aws.String(bucket),
		Key:         aws.String(key),
		Body:        strings.NewReader(string(raw)),
		ContentType: aws.String(contentTypeForExt(ext)),
	}); err != nil {
		return errJSON(500, "s3 put: "+err.Error()), nil
	}

	now := time.Now().UTC().Format(time.RFC3339)
	if _, err := ddbClient.PutItem(ctx, &dynamodb.PutItemInput{
		TableName: aws.String(jobsTable),
		Item: map[string]ddbtypes.AttributeValue{
			"job_id":     &ddbtypes.AttributeValueMemberS{Value: jobID},
			"status":     &ddbtypes.AttributeValueMemberS{Value: "pending"},
			"image_key":  &ddbtypes.AttributeValueMemberS{Value: key},
			"created_at": &ddbtypes.AttributeValueMemberS{Value: now},
			"updated_at": &ddbtypes.AttributeValueMemberS{Value: now},
		},
	}); err != nil {
		return errJSON(500, "ddb put: "+err.Error()), nil
	}

	msg, _ := json.Marshal(map[string]string{"job_id": jobID, "image_key": key})
	if _, err := sqsClient.SendMessage(ctx, &sqs.SendMessageInput{
		QueueUrl:    aws.String(yoloQueueURL),
		MessageBody: aws.String(string(msg)),
	}); err != nil {
		return errJSON(500, "sqs send: "+err.Error()), nil
	}

	return okJSON(202, map[string]any{"job_id": jobID}), nil
}

// ---- POST /api/scan/init ----
// presigned PUT URL を発行し、フロントから S3 に直接アップロードさせる。
// API Gateway の 10MB ペイロード制限を回避する。
// body: { filename: string, content_type?: string }
// resp: { job_id, upload_url, key, content_type }
func scanInit(ctx context.Context, req events.APIGatewayV2HTTPRequest) (events.APIGatewayV2HTTPResponse, error) {
	body := decodeBody(req)
	var payload struct {
		Filename    string `json:"filename"`
		ContentType string `json:"content_type"`
	}
	if err := json.Unmarshal([]byte(body), &payload); err != nil {
		return errJSON(400, "expected JSON {filename, content_type?}"), nil
	}
	if payload.Filename == "" {
		return errJSON(400, "filename is required"), nil
	}
	ext := strings.ToLower(filepath.Ext(payload.Filename))
	if ext == "" {
		ext = ".jpg"
	}
	ct := payload.ContentType
	if ct == "" {
		ct = contentTypeForExt(ext)
	}
	jobID := uuid.New().String()
	key := fmt.Sprintf("uploads/%s/upload%s", jobID, ext)

	presigner := s3.NewPresignClient(s3Client)
	presigned, err := presigner.PresignPutObject(ctx, &s3.PutObjectInput{
		Bucket:      aws.String(bucket),
		Key:         aws.String(key),
		ContentType: aws.String(ct),
	}, func(o *s3.PresignOptions) { o.Expires = 15 * time.Minute })
	if err != nil {
		return errJSON(500, "presign: "+err.Error()), nil
	}

	now := time.Now().UTC().Format(time.RFC3339)
	if _, err := ddbClient.PutItem(ctx, &dynamodb.PutItemInput{
		TableName: aws.String(jobsTable),
		Item: map[string]ddbtypes.AttributeValue{
			"job_id":     &ddbtypes.AttributeValueMemberS{Value: jobID},
			"status":     &ddbtypes.AttributeValueMemberS{Value: "uploading"},
			"image_key":  &ddbtypes.AttributeValueMemberS{Value: key},
			"created_at": &ddbtypes.AttributeValueMemberS{Value: now},
			"updated_at": &ddbtypes.AttributeValueMemberS{Value: now},
		},
	}); err != nil {
		return errJSON(500, "ddb put: "+err.Error()), nil
	}

	return okJSON(200, map[string]any{
		"job_id":       jobID,
		"upload_url":   presigned.URL,
		"key":          key,
		"content_type": ct,
	}), nil
}

// ---- POST /api/scan/start ----
// scanInit でアップロード完了後、SQS にメッセージを投入して処理開始。
// body: { job_id }
func scanStart(ctx context.Context, req events.APIGatewayV2HTTPRequest) (events.APIGatewayV2HTTPResponse, error) {
	body := decodeBody(req)
	var payload struct {
		JobID string `json:"job_id"`
	}
	if err := json.Unmarshal([]byte(body), &payload); err != nil || payload.JobID == "" {
		return errJSON(400, "expected JSON {job_id}"), nil
	}
	out, err := ddbClient.GetItem(ctx, &dynamodb.GetItemInput{
		TableName: aws.String(jobsTable),
		Key: map[string]ddbtypes.AttributeValue{
			"job_id": &ddbtypes.AttributeValueMemberS{Value: payload.JobID},
		},
	})
	if err != nil || out.Item == nil {
		return errJSON(404, "job not found"), nil
	}
	var key string
	if v, ok := out.Item["image_key"].(*ddbtypes.AttributeValueMemberS); ok {
		key = v.Value
	}
	if key == "" {
		return errJSON(400, "image_key missing on job"), nil
	}
	// S3 にオブジェクトが届いているか軽く確認
	if _, err := s3Client.HeadObject(ctx, &s3.HeadObjectInput{Bucket: aws.String(bucket), Key: aws.String(key)}); err != nil {
		return errJSON(400, "upload not found in S3: "+err.Error()), nil
	}
	now := time.Now().UTC().Format(time.RFC3339)
	if _, err := ddbClient.UpdateItem(ctx, &dynamodb.UpdateItemInput{
		TableName: aws.String(jobsTable),
		Key: map[string]ddbtypes.AttributeValue{
			"job_id": &ddbtypes.AttributeValueMemberS{Value: payload.JobID},
		},
		UpdateExpression:         aws.String("SET #s = :s, updated_at = :u"),
		ExpressionAttributeNames: map[string]string{"#s": "status"},
		ExpressionAttributeValues: map[string]ddbtypes.AttributeValue{
			":s": &ddbtypes.AttributeValueMemberS{Value: "pending"},
			":u": &ddbtypes.AttributeValueMemberS{Value: now},
		},
	}); err != nil {
		return errJSON(500, "ddb update: "+err.Error()), nil
	}
	msg, _ := json.Marshal(map[string]string{"job_id": payload.JobID, "image_key": key})
	if _, err := sqsClient.SendMessage(ctx, &sqs.SendMessageInput{
		QueueUrl:    aws.String(yoloQueueURL),
		MessageBody: aws.String(string(msg)),
	}); err != nil {
		return errJSON(500, "sqs send: "+err.Error()), nil
	}
	return okJSON(202, map[string]any{"job_id": payload.JobID}), nil
}

func decodeBody(req events.APIGatewayV2HTTPRequest) string {
	if req.IsBase64Encoded {
		if dec, err := base64.StdEncoding.DecodeString(req.Body); err == nil {
			return string(dec)
		}
	}
	return req.Body
}

// ---- GET /api/jobs/{id} ----
func getJob(ctx context.Context, req events.APIGatewayV2HTTPRequest) (events.APIGatewayV2HTTPResponse, error) {
	id := strings.Trim(strings.TrimPrefix(requestPath(req), "/api/jobs/"), "/")
	if id == "" || id == requestPath(req) {
		id = filepath.Base(requestPath(req))
	}
	out, err := ddbClient.GetItem(ctx, &dynamodb.GetItemInput{
		TableName: aws.String(jobsTable),
		Key: map[string]ddbtypes.AttributeValue{
			"job_id": &ddbtypes.AttributeValueMemberS{Value: id},
		},
	})
	if err != nil || out.Item == nil {
		return errJSON(404, "job not found"), nil
	}

	resp := map[string]any{"job_id": id}
	// Map/List も含めて全属性を一般的な Go の型に展開する
	var generic map[string]any
	if err := attributevalue.UnmarshalMap(out.Item, &generic); err == nil {
		for k, v := range generic {
			resp[k] = v
		}
	}

	// Live scan writes a partial catalog while status=collecting/processing.
	// Return it as soon as catalog_key exists so the camera UI can update.
	if key, _ := resp["catalog_key"].(string); key != "" {
		obj, err := s3Client.GetObject(ctx, &s3.GetObjectInput{Bucket: aws.String(bucket), Key: aws.String(key)})
		if err == nil {
			defer obj.Body.Close()
			var catalog any
			if err := json.NewDecoder(obj.Body).Decode(&catalog); err == nil {
				resp["catalog"] = catalog
			}
		}
	} else if status, _ := resp["status"].(string); status == "done" {
		key := fmt.Sprintf("catalogs/%s/catalog.json", id)
		obj, err := s3Client.GetObject(ctx, &s3.GetObjectInput{Bucket: aws.String(bucket), Key: aws.String(key)})
		if err == nil {
			defer obj.Body.Close()
			var catalog any
			if err := json.NewDecoder(obj.Body).Decode(&catalog); err == nil {
				resp["catalog"] = catalog
			}
		}
	}
	return okJSON(200, resp), nil
}

func requestPath(req events.APIGatewayV2HTTPRequest) string {
	if req.RawPath != "" {
		return req.RawPath
	}
	return req.RequestContext.HTTP.Path
}

// ---- GET /api/crops/{job_id}/{crop_id}.jpg ----
// catalog.json は s3://bucket/crops/{job_id}/{crop_id}.jpg を返すが、
// ブラウザから直接 S3 は触れないので Lambda 経由でストリーミングする。
func getCrop(ctx context.Context, rawPath string) (events.APIGatewayV2HTTPResponse, error) {
	key := strings.TrimPrefix(rawPath, "/api/crops/")
	if key == "" || strings.Contains(key, "..") {
		return errJSON(400, "invalid crop key"), nil
	}
	s3Key := "crops/" + key
	obj, err := s3Client.GetObject(ctx, &s3.GetObjectInput{Bucket: aws.String(bucket), Key: aws.String(s3Key)})
	if err != nil {
		return errJSON(404, "crop not found"), nil
	}
	defer obj.Body.Close()
	buf := make([]byte, 0, 1024*1024)
	tmp := make([]byte, 64*1024)
	for {
		n, rerr := obj.Body.Read(tmp)
		if n > 0 {
			buf = append(buf, tmp[:n]...)
		}
		if rerr != nil {
			break
		}
	}
	ct := "image/jpeg"
	if obj.ContentType != nil && *obj.ContentType != "" {
		ct = *obj.ContentType
	}
	return events.APIGatewayV2HTTPResponse{
		StatusCode: 200,
		Headers: map[string]string{
			"Content-Type":                ct,
			"Cache-Control":               "public, max-age=300",
			"Access-Control-Allow-Origin": "*",
		},
		Body:            base64.StdEncoding.EncodeToString(buf),
		IsBase64Encoded: true,
	}, nil
}

// ---- GET /api/shelves ----
func getShelves(ctx context.Context) (events.APIGatewayV2HTTPResponse, error) {
	key := "assets/apriltag_library_map.json"
	obj, err := s3Client.GetObject(ctx, &s3.GetObjectInput{Bucket: aws.String(bucket), Key: aws.String(key)})
	if err != nil {
		return okJSON(200, map[string]any{"shelves": []any{}}), nil
	}
	defer obj.Body.Close()
	var mapping any
	if err := json.NewDecoder(obj.Body).Decode(&mapping); err != nil {
		return okJSON(200, map[string]any{"shelves": []any{}}), nil
	}
	return okJSON(200, mapping), nil
}

// ---- helpers ----
var safeRe = regexp.MustCompile(`[^a-zA-Z0-9._-]`)

func sanitize(s string) string { return safeRe.ReplaceAllString(s, "_") }

func contentTypeForExt(ext string) string {
	switch ext {
	case ".jpg", ".jpeg":
		return "image/jpeg"
	case ".png":
		return "image/png"
	case ".webp":
		return "image/webp"
	case ".mp4":
		return "video/mp4"
	case ".mov":
		return "video/quicktime"
	case ".webm":
		return "video/webm"
	}
	return "application/octet-stream"
}

func okJSON(status int, v any) events.APIGatewayV2HTTPResponse {
	headers := map[string]string{
		"Content-Type":                "application/json; charset=utf-8",
		"Access-Control-Allow-Origin": "*",
	}
	if v == nil {
		return events.APIGatewayV2HTTPResponse{StatusCode: status, Headers: headers}
	}
	b, _ := json.Marshal(v)
	return events.APIGatewayV2HTTPResponse{StatusCode: status, Headers: headers, Body: string(b)}
}

func errJSON(status int, msg string) events.APIGatewayV2HTTPResponse {
	return okJSON(status, map[string]string{"error": msg})
}
