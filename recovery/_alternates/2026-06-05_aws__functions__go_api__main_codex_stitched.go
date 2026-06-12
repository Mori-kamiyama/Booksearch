package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/aws/aws-lambda-go/events"
	"github.com/aws/aws-lambda-go/lambda"
	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	ddbtypes "github.com/aws/aws-sdk-go-v2/service/dynamodb/types"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/sqs"
	"github.com/google/uuid"
)

var (
	bucket        string
	jobsTable     string
	yoloQueueURL  string
	s3Client      *s3.Client
	ddbClient     *dynamodb.Client
	sqsClient     *sqs.Client
	bookStore     *BookStore
	staticAssets  string
)

func init() {
	bucket = os.Getenv("BUCKET")
	jobsTable = os.Getenv("JOBS_TABLE")
	yoloQueueURL = os.Getenv("YOLO_QUEUE_URL")
	staticAssets = os.Getenv("LAMBDA_TASK_ROOT")

	cfg, err := awsconfig.LoadDefaultConfig(context.Background())
	if err != nil {
		log.Fatalf("aws config: %v", err)
	}
	s3Client = s3.NewFromConfig(cfg)
	ddbClient = dynamodb.NewFromConfig(cfg)
	sqsClient = sqs.NewFromConfig(cfg)

	dbPath := filepath.Join(staticAssets, "library.db")
	bookStore, err = OpenBookStore(dbPath)
	if err != nil {
		log.Printf("warn: library.db not available: %v", err)
	}
}

func main() {
	lambda.Start(handler)
}

func handler(ctx context.Context, req events.APIGatewayV2HTTPRequest) (events.APIGatewayV2HTTPResponse, error) {
	method := req.RequestContext.HTTP.Method
	path := req.RawPath
	if path == "" {
		path = req.RequestContext.HTTP.Path
	}

	if method == "OPTIONS" {
		return okJSON(204, nil), nil
	}

	switch {
	case method == "GET" && path == "/api/health":
		return okJSON(200, map[string]any{"status": "ok", "time": time.Now().Unix()}), nil
	case method == "GET" && path == "/api/books/search":
		return searchBooks(req)
	case method == "GET" && strings.HasPrefix(path, "/api/books/"):
		return getBook(req)
	case method == "GET" && strings.HasPrefix(path, "/api/jobs/"):
		return getJob(ctx, req)
	case method == "POST" && path == "/api/scan":
		return scan(ctx, req)
	case method == "GET" && path == "/api/shelves":
		return getShelves(ctx)
	default:
		return errJSON(404, "not found"), nil
	}
}

// ---- /api/books/search ----
func searchBooks(req events.APIGatewayV2HTTPRequest) (events.APIGatewayV2HTTPResponse, error) {
	q := req.QueryStringParameters["q"]
	if q == "" {
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
	books, err := bookStore.Search(q, limit)
	if err != nil {
		return errJSON(500, err.Error()), nil
	}
	return okJSON(200, map[string]any{"books": books, "query": q}), nil
}

// ---- /api/books/{id} ----
func getBook(req events.APIGatewayV2HTTPRequest) (events.APIGatewayV2HTTPResponse, error) {
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
	return okJSON(200, book), nil
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

// ---- GET /api/jobs/{id} ----
func getJob(ctx context.Context, req events.APIGatewayV2HTTPRequest) (events.APIGatewayV2HTTPResponse, error) {
	id := filepath.Base(req.RawPath)
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
	for k, v := range out.Item {
		if s, ok := v.(*ddbtypes.AttributeValueMemberS); ok {
			resp[k] = s.Value
		}
		if n, ok := v.(*ddbtypes.AttributeValueMemberN); ok {
			resp[k] = n.Value
		}
	}

	// status=done なら catalog.json を S3 から取って返す
	if status, _ := resp["status"].(string); status == "done" {
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

// ---- GET /api/shelves ----
func getShelves(ctx context.Context) (events.APIGatewayV2HTTPResponse, error) {
	key := "assets/apriltag_shelf_map.json"
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
