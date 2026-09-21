package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/aws/aws-lambda-go/events"
	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

func TestFrameAcceptanceIsAnAtomicTaskWithoutQueueSend(t *testing.T) {
	var transaction map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodHead {
			w.WriteHeader(200)
			return
		}
		if !strings.HasSuffix(r.Header.Get("X-Amz-Target"), ".TransactWriteItems") {
			t.Errorf("unexpected call %s", r.Header.Get("X-Amz-Target"))
			w.WriteHeader(500)
			return
		}
		if err := json.NewDecoder(r.Body).Decode(&transaction); err != nil {
			t.Error(err)
		}
		writeDynamoJSON(w, 200, "{}")
	}))
	defer server.Close()
	oldDDB, oldS3, oldSQS, oldTasks, oldJobs, oldBucket := ddbClient, s3Client, sqsClient, scanTasksTable, jobsTable, bucket
	t.Cleanup(func() {
		ddbClient, s3Client, sqsClient, scanTasksTable, jobsTable, bucket = oldDDB, oldS3, oldSQS, oldTasks, oldJobs, oldBucket
	})
	ddbClient, _ = testAWSClients(server.URL)
	sqsClient = nil
	scanTasksTable, jobsTable, bucket = "tasks", "jobs", "assets"
	s3Client = s3.NewFromConfig(aws.Config{Region: "us-east-1", Credentials: credentials.NewStaticCredentialsProvider("test", "test", ""), Retryer: func() aws.Retryer { return aws.NopRetryer{} }}, func(o *s3.Options) { o.BaseEndpoint = aws.String(server.URL); o.UsePathStyle = true })
	response, err := liveSessionFrameCommit(context.Background(), events.APIGatewayV2HTTPRequest{Body: `{"frame_key":"live/session/frames/a.jpg"}`}, "/api/scan/sessions/session/commit-frame")
	if err != nil || response.StatusCode != 202 {
		t.Fatalf("response %v error %v", response, err)
	}
	writes := transaction["TransactItems"].([]any)
	if len(writes) != 2 {
		t.Fatalf("writes=%v", writes)
	}
	update := writes[0].(map[string]any)["Update"].(map[string]any)
	if !strings.Contains(update["ConditionExpression"].(string), "contains(frame_keys") {
		t.Fatalf("unissued frame accepted: %v", update)
	}
	put := writes[1].(map[string]any)["Put"].(map[string]any)
	item := put["Item"].(map[string]any)
	if item["task_id"].(map[string]any)["S"] != frameTaskID("live/session/frames/a.jpg") || item["state"].(map[string]any)["S"] != "pending" {
		t.Fatalf("bad durable task %v", item)
	}
}
