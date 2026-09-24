package main

import (
	"testing"

	"github.com/aws/aws-lambda-go/events"
	ddbtypes "github.com/aws/aws-sdk-go-v2/service/dynamodb/types"
)

func TestScanStartTransactionAtomicallyChangesStateAndCreatesOutbox(t *testing.T) {
	jobsTable, outboxTable = "jobs", "outbox"
	items := scanStartTransaction("job-1", "uploads/job-1/upload.jpg", "now")
	if len(items) != 2 {
		t.Fatalf("transaction item count = %d, want 2", len(items))
	}
	update := items[0].Update
	put := items[1].Put
	if update == nil || put == nil || *update.ConditionExpression != "#s = :uploading" {
		t.Fatalf("expected conditional uploading -> pending update: %#v", update)
	}
	if *put.TableName != "outbox" || *put.ConditionExpression != "attribute_not_exists(job_id)" {
		t.Fatalf("expected conditional yolo outbox put: %#v", put)
	}
	if event, ok := put.Item["event_type"].(*ddbtypes.AttributeValueMemberS); !ok || event.Value != "yolo" {
		t.Fatalf("outbox event = %#v, want yolo", put.Item["event_type"])
	}
}

func TestUploadPolicyAndOwnerAuthorization(t *testing.T) {
	conditions := uploadPolicyConditions()
	if len(conditions) != 1 || conditions[0].([]interface{})[0] != "content-length-range" {
		t.Fatalf("missing browser-compatible content-length-range policy: %#v", conditions)
	}
	request := events.APIGatewayV2HTTPRequest{RequestContext: events.APIGatewayV2HTTPRequestContext{
		Authorizer: &events.APIGatewayV2HTTPRequestContextAuthorizerDescription{
			JWT: &events.APIGatewayV2HTTPRequestContextAuthorizerJWTDescription{Claims: map[string]string{"sub": "user-1"}},
		},
	}}
	if !authorizedScan(request) || requestOwner(request) != "user-1" {
		t.Fatal("expected JWT subject to authorize scan")
	}
	if authorizedScan(events.APIGatewayV2HTTPRequest{}) {
		t.Fatal("unauthenticated request must fail closed")
	}
}
