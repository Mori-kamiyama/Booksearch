package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/sqs"
)

func testAWSClients(endpoint string) (*dynamodb.Client, *sqs.Client) {
	cfg := aws.Config{
		Region:      "us-east-1",
		Credentials: credentials.NewStaticCredentialsProvider("test", "test", ""),
		Retryer:     func() aws.Retryer { return aws.NopRetryer{} },
	}
	ddb := dynamodb.NewFromConfig(cfg, func(options *dynamodb.Options) {
		options.BaseEndpoint = aws.String(endpoint)
	})
	sqsClient := sqs.NewFromConfig(cfg, func(options *sqs.Options) {
		options.BaseEndpoint = aws.String(endpoint)
	})
	return ddb, sqsClient
}

func writeDynamoJSON(w http.ResponseWriter, status int, body string) {
	w.Header().Set("Content-Type", "application/x-amz-json-1.0")
	w.WriteHeader(status)
	_, _ = io.WriteString(w, body)
}

func shelfCandidateItem(id int) map[string]map[string]string {
	return map[string]map[string]string{
		"book_id":    {"N": strconv.Itoa(id)},
		"shelf_id":   {"S": "base-01"},
		"confidence": {"N": "0.9"},
	}
}

func TestListShelfCandidatesPaginatesPast500(t *testing.T) {
	var scans atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.Header.Get("X-Amz-Target"), ".Scan") {
			t.Fatalf("unexpected AWS target %q", r.Header.Get("X-Amz-Target"))
		}
		page := scans.Add(1)
		if page == 1 {
			items := make([]map[string]map[string]string, 500)
			for i := range items {
				items[i] = shelfCandidateItem(i + 1)
			}
			body, _ := json.Marshal(map[string]any{
				"Items":            items,
				"LastEvaluatedKey": map[string]map[string]string{"book_id": {"N": "500"}, "shelf_id": {"S": "base-01"}},
			})
			writeDynamoJSON(w, http.StatusOK, string(body))
			return
		}
		body, _ := json.Marshal(map[string]any{"Items": []map[string]map[string]string{shelfCandidateItem(501)}})
		writeDynamoJSON(w, http.StatusOK, string(body))
	}))
	defer server.Close()

	oldClient, oldTable, oldStore := ddbClient, shelfCandidatesTable, bookStore
	t.Cleanup(func() {
		ddbClient, shelfCandidatesTable, bookStore = oldClient, oldTable, oldStore
	})
	ddbClient, _ = testAWSClients(server.URL)
	shelfCandidatesTable = "candidates"
	bookStore = nil

	response, err := listShelfCandidates(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", response.StatusCode)
	}
	var payload struct {
		Candidates []ShelfCandidate
	}
	if err := json.Unmarshal([]byte(response.Body), &payload); err != nil {
		t.Fatal(err)
	}
	if len(payload.Candidates) != 501 {
		t.Fatalf("got %d candidates, want 501", len(payload.Candidates))
	}
	if got := scans.Load(); got != 2 {
		t.Fatalf("scan calls = %d, want 2", got)
	}
}

func TestListShelfCandidatesReturnsMidPaginationError(t *testing.T) {
	var scans atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if scans.Add(1) == 1 {
			writeDynamoJSON(w, http.StatusOK, "{\"Items\":[],\"LastEvaluatedKey\":{\"book_id\":{\"N\":\"500\"}}}")
			return
		}
		writeDynamoJSON(w, http.StatusInternalServerError, "{\"__type\":\"InternalServerError\",\"message\":\"temporary\"}")
	}))
	defer server.Close()

	oldClient, oldTable, oldStore := ddbClient, shelfCandidatesTable, bookStore
	t.Cleanup(func() {
		ddbClient, shelfCandidatesTable, bookStore = oldClient, oldTable, oldStore
	})
	ddbClient, _ = testAWSClients(server.URL)
	shelfCandidatesTable = "candidates"
	bookStore = nil

	response, err := listShelfCandidates(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500", response.StatusCode)
	}
	if got := scans.Load(); got != 2 {
		t.Fatalf("scan calls = %d, want 2", got)
	}
}

func TestLiveSessionCompleteRetriesFinalLookupAfterWorkersFinish(t *testing.T) {
	var updates, messages atomic.Int32
	var updateBody []byte
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		target := r.Header.Get("X-Amz-Target")
		switch {
		case strings.HasSuffix(target, ".GetItem"):
			writeDynamoJSON(w, http.StatusOK, "{\"Item\":{\"job_id\":{\"S\":\"session-1\"},\"status\":{\"S\":\"processing\"},\"accepted_frames\":{\"N\":\"1\"},\"processed_frames\":{\"N\":\"1\"},\"ocr_total\":{\"N\":\"1\"},\"ocr_done\":{\"N\":\"1\"}}}")
		case strings.HasSuffix(target, ".UpdateItem"):
			updates.Add(1)
			updateBody, _ = io.ReadAll(r.Body)
			writeDynamoJSON(w, http.StatusOK, "{}")
		case strings.HasSuffix(target, ".SendMessage"):
			messages.Add(1)
			writeDynamoJSON(w, http.StatusOK, "{\"MessageId\":\"message-1\"}")
		default:
			t.Fatalf("unexpected AWS target %q", target)
		}
	}))
	defer server.Close()

	oldDDB, oldSQS, oldTable, oldQueue := ddbClient, sqsClient, jobsTable, lookupQueueURL
	t.Cleanup(func() {
		ddbClient, sqsClient, jobsTable, lookupQueueURL = oldDDB, oldSQS, oldTable, oldQueue
	})
	ddbClient, sqsClient = testAWSClients(server.URL)
	jobsTable, lookupQueueURL = "jobs", "https://queue.invalid/final"

	response, err := liveSessionComplete(context.Background(), "/api/scan/sessions/session-1/complete")
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusAccepted {
		t.Fatalf("status = %d, want 202", response.StatusCode)
	}
	if updates.Load() != 1 || messages.Load() != 0 {
		t.Fatalf("final lookup calls = update %d/send %d, want 1/0", updates.Load(), messages.Load())
	}
	var request struct {
		ConditionExpression       string                    `json:"ConditionExpression"`
		UpdateExpression          string                    `json:"UpdateExpression"`
		ExpressionAttributeValues map[string]map[string]any `json:"ExpressionAttributeValues"`
	}
	if err := json.Unmarshal(updateBody, &request); err != nil {
		t.Fatal(err)
	}
	if request.ConditionExpression != "attribute_not_exists(final_lookup_outbox_version) AND #s = :processing" {
		t.Fatalf("condition = %q", request.ConditionExpression)
	}
	if request.UpdateExpression != "SET final_lookup_queued = :yes, final_lookup_outbox_version = :version, #s = :pending" {
		t.Fatalf("update = %q", request.UpdateExpression)
	}
	values := request.ExpressionAttributeValues
	if values[":version"]["N"] != "1" || values[":pending"]["S"] != "lookup_pending" || values[":processing"]["S"] != "processing" {
		t.Fatalf("outbox values = %#v", values)
	}
}

func TestLiveSessionCompleteDoneIsIdempotent(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if !strings.HasSuffix(r.Header.Get("X-Amz-Target"), ".GetItem") {
			t.Fatalf("unexpected extra AWS request %q", r.Header.Get("X-Amz-Target"))
		}
		writeDynamoJSON(w, http.StatusOK, "{\"Item\":{\"job_id\":{\"S\":\"session-done\"},\"status\":{\"S\":\"done\"}}}")
	}))
	defer server.Close()

	oldDDB, oldTable := ddbClient, jobsTable
	t.Cleanup(func() { ddbClient, jobsTable = oldDDB, oldTable })
	ddbClient, _ = testAWSClients(server.URL)
	jobsTable = "jobs"

	response, err := liveSessionComplete(context.Background(), "/api/scan/sessions/session-done/complete")
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusAccepted {
		t.Fatalf("status = %d, want 202", response.StatusCode)
	}
	if calls.Load() != 1 {
		t.Fatalf("AWS calls = %d, want 1", calls.Load())
	}
}

func TestLiveSessionCancelClosedDoesNotDeleteS3(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if !strings.HasSuffix(r.Header.Get("X-Amz-Target"), ".GetItem") {
			t.Fatalf("unexpected extra AWS request %q", r.Header.Get("X-Amz-Target"))
		}
		writeDynamoJSON(w, http.StatusOK, "{\"Item\":{\"job_id\":{\"S\":\"session-done\"},\"status\":{\"S\":\"done\"},\"frame_keys\":{\"L\":[{\"S\":\"live/session-done/frames/frame.jpg\"}]}}}")
	}))
	defer server.Close()

	oldDDB, oldS3, oldTable, oldBucket := ddbClient, s3Client, jobsTable, bucket
	t.Cleanup(func() { ddbClient, s3Client, jobsTable, bucket = oldDDB, oldS3, oldTable, oldBucket })
	ddbClient, _ = testAWSClients(server.URL)
	s3Client = nil
	jobsTable, bucket = "jobs", "bucket"

	response, err := liveSessionCancel(context.Background(), "/api/scan/sessions/session-done/cancel")
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusConflict {
		t.Fatalf("status = %d, want 409", response.StatusCode)
	}
	if calls.Load() != 1 {
		t.Fatalf("AWS calls = %d, want only GetItem", calls.Load())
	}
}

func TestLiveSessionCompleteConcurrentCancellationIsNotSuccess(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.Header.Get("X-Amz-Target"), ".GetItem"):
			writeDynamoJSON(w, 200, `{"Item":{"status":{"S":"collecting"},"committed_frame_keys":{"SS":["frame"]}}}`)
		case strings.HasSuffix(r.Header.Get("X-Amz-Target"), ".UpdateItem"):
			writeDynamoJSON(w, 400, `{"__type":"ConditionalCheckFailedException","message":"cancel won"}`)
		default:
			t.Errorf("unexpected call %s", r.Header.Get("X-Amz-Target"))
			writeDynamoJSON(w, 500, `{}`)
		}
	}))
	defer server.Close()
	oldClient, oldTable := ddbClient, jobsTable
	t.Cleanup(func() { ddbClient, jobsTable = oldClient, oldTable })
	ddbClient, _ = testAWSClients(server.URL)
	jobsTable = "jobs"
	response, err := liveSessionComplete(context.Background(), "/api/scan/sessions/session-1/complete")
	if err != nil || response.StatusCode != 409 {
		t.Fatalf("got %d / %v; expected conflict", response.StatusCode, err)
	}
}
