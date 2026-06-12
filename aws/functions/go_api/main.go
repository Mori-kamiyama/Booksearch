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
	s3Client             *s3.Client
	ddbClient            *dynamodb.Client
	sqsClient            *sqs.Client
	bookStore            *BookStore
	staticAssets         string
)

func init() {
	bucket = os.Getenv("BUCKET")
	jobsTable = os.Getenv("JOBS_TABLE")
	shelfCandidatesTable = os.Getenv("SHELF_CANDIDATES_TABLE")
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
	case method == "GET" && path == "/api/shelves":
		return getShelves(ctx)
	case method == "GET" && strings.HasPrefix(path, "/api/crops/"):
		return getCrop(ctx, path)
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
	attachShelfCandidates(context.Background(), books)
	return okJSON(200, map[string]any{"books": books, "query": q}), nil
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

func fetchShelfCandidates(ctx context.Context, bookID int) ([]ShelfCandidate, error) {
	out, err := ddbClient.Query(ctx, &dynamodb.QueryInput{
		TableName:              aws.String(shelfCandidatesTable),
		KeyConditionExpression: aws.String("book_id = :b"),
		ExpressionAttributeValues: map[string]ddbtypes.AttributeValue{
			":b": &ddbtypes.AttributeValueMemberN{Value: strconv.Itoa(bookID)},
		},
	})
	if err != nil {
		return nil, err
	}
	candidates := make([]ShelfCandidate, 0, len(out.Items))
	for _, item := range out.Items {
		candidates = append(candidates, shelfCandidateFromItem(item))
	}
	return candidates, nil
}

func listShelfCandidates(ctx context.Context) (events.APIGatewayV2HTTPResponse, error) {
	if shelfCandidatesTable == "" {
		return okJSON(200, map[string]any{"candidates": []any{}}), nil
	}
	out, err := ddbClient.Scan(ctx, &dynamodb.ScanInput{
		TableName: aws.String(shelfCandidatesTable),
		Limit:     aws.Int32(500),
	})
	if err != nil {
		return errJSON(500, err.Error()), nil
	}
	candidates := make([]ShelfCandidate, 0, len(out.Items))
	for _, item := range out.Items {
		candidates = append(candidates, shelfCandidateFromItem(item))
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
	// Map/List も含めて全属性を一般的な Go の型に展開する
	var generic map[string]any
	if err := attributevalue.UnmarshalMap(out.Item, &generic); err == nil {
		for k, v := range generic {
			resp[k] = v
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
