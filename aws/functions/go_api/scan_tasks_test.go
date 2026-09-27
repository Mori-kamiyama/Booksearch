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

func TestFrameInitializationRetryReusesKeyAndRejectsClosedSession(t *testing.T) {
	var key string
	closed := false
	updates := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		json.NewDecoder(r.Body).Decode(&body)
		switch {
		case strings.HasSuffix(r.Header.Get("X-Amz-Target"), ".UpdateItem"):
			values := body["ExpressionAttributeValues"].(map[string]any)
			got := values[":key"].(map[string]any)["S"].(string)
			if key == "" {
				key = got
			} else if key != got {
				t.Errorf("retry changed key: %s != %s", key, got)
			}
			if !strings.Contains(body["ConditionExpression"].(string), "NOT contains(frame_keys") {
				t.Error("missing duplicate guard")
			}
			updates++
			if updates == 1 {
				writeDynamoJSON(w, 200, `{}`)
			} else {
				writeDynamoJSON(w, 400, `{"__type":"ConditionalCheckFailedException","message":"duplicate or closed"}`)
			}
		case strings.HasSuffix(r.Header.Get("X-Amz-Target"), ".GetItem"):
			state := "collecting"
			if closed {
				state = "canceled"
			}
			data, _ := json.Marshal(map[string]any{"Item": map[string]any{"status": map[string]string{"S": state}, "frame_keys": map[string]any{"L": []any{map[string]string{"S": key}}}}})
			writeDynamoJSON(w, 200, string(data))
		default:
			t.Errorf("unexpected API %s", r.Header.Get("X-Amz-Target"))
			w.WriteHeader(500)
		}
	}))
	defer server.Close()
	oldDDB, oldS3, oldJobs, oldBucket := ddbClient, s3Client, jobsTable, bucket
	t.Cleanup(func() { ddbClient, s3Client, jobsTable, bucket = oldDDB, oldS3, oldJobs, oldBucket })
	ddbClient, _ = testAWSClients(server.URL)
	jobsTable, bucket = "jobs", "assets"
	s3Client = s3.NewFromConfig(aws.Config{Region: "us-east-1", Credentials: credentials.NewStaticCredentialsProvider("test", "test", "")})
	for i, want := range []int{200, 200, 409} {
		closed = i == 2
		response, err := liveSessionFrame(context.Background(), events.APIGatewayV2HTTPRequest{Body: `{"filename":"frame_000001.jpg"}`}, "/api/scan/sessions/session/frames")
		if err != nil || response.StatusCode != want {
			t.Fatalf("attempt %d: %v %v", i, response, err)
		}
	}
}
