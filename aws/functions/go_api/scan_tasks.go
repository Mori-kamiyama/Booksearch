package main

import (
	"context"
	"crypto/sha256"
	"fmt"
	"os"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	ddbtypes "github.com/aws/aws-sdk-go-v2/service/dynamodb/types"
)

var scanTasksTable = os.Getenv("SCAN_TASKS_TABLE")

func detectionTask(jobID, taskID, imageKey, now string, incremental bool) map[string]ddbtypes.AttributeValue {
	return map[string]ddbtypes.AttributeValue{
		"job_id":      &ddbtypes.AttributeValueMemberS{Value: jobID},
		"task_id":     &ddbtypes.AttributeValueMemberS{Value: taskID},
		"state":       &ddbtypes.AttributeValueMemberS{Value: "pending"},
		"image_keys":  &ddbtypes.AttributeValueMemberL{Value: []ddbtypes.AttributeValue{&ddbtypes.AttributeValueMemberS{Value: imageKey}}},
		"incremental": &ddbtypes.AttributeValueMemberBOOL{Value: incremental},
		"created_at":  &ddbtypes.AttributeValueMemberS{Value: now},
	}
}

func frameTaskID(key string) string { return fmt.Sprintf("frame:%x", sha256.Sum256([]byte(key))) }

func persistDetectionTask(ctx context.Context, jobChange ddbtypes.TransactWriteItem, task map[string]ddbtypes.AttributeValue) error {
	if scanTasksTable == "" {
		return fmt.Errorf("scan task storage is not configured")
	}
	_, err := ddbClient.TransactWriteItems(ctx, &dynamodb.TransactWriteItemsInput{TransactItems: []ddbtypes.TransactWriteItem{
		jobChange,
		{Put: &ddbtypes.Put{TableName: aws.String(scanTasksTable), Item: task, ConditionExpression: aws.String("attribute_not_exists(job_id)")}},
	}})
	return err
}

func savedDetectionTask(ctx context.Context, jobID, taskID string) (bool, error) {
	out, err := ddbClient.GetItem(ctx, &dynamodb.GetItemInput{
		TableName: aws.String(scanTasksTable), ConsistentRead: aws.Bool(true),
		Key: map[string]ddbtypes.AttributeValue{"job_id": &ddbtypes.AttributeValueMemberS{Value: jobID}, "task_id": &ddbtypes.AttributeValueMemberS{Value: taskID}},
	})
	return err == nil && len(out.Item) != 0, err
}
