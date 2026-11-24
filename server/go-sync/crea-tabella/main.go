// crea-tabella prepares the DynamoDB Local table that go-sync needs, from
// go-sync's own schema (schema/dynamodb/table.json). It leaves an existing
// table alone, so it can run at every start of the server.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"os"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb/types"
)

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()

	schemaPath := os.Getenv("SCHEMA")
	if schemaPath == "" {
		schemaPath = "/etc/go-sync/table.json"
	}
	schema, err := os.ReadFile(schemaPath)
	if err != nil {
		log.Fatalf("schema: %v", err)
	}
	var input dynamodb.CreateTableInput
	if err := json.Unmarshal(schema, &input); err != nil {
		log.Fatalf("schema: %v", err)
	}
	table := os.Getenv("TABLE_NAME")
	if table == "" {
		log.Fatal("TABLE_NAME is empty")
	}
	input.TableName = aws.String(table)

	cfg, err := config.LoadDefaultConfig(ctx, config.WithRegion(os.Getenv("AWS_REGION")))
	if err != nil {
		log.Fatalf("config: %v", err)
	}
	db := dynamodb.NewFromConfig(cfg, func(o *dynamodb.Options) {
		o.BaseEndpoint = aws.String(os.Getenv("AWS_ENDPOINT"))
	})

	// DynamoDB Local needs a few seconds after its container starts.
	for {
		_, err = db.CreateTable(ctx, &input)
		var inUse *types.ResourceInUseException
		if err == nil || errors.As(err, &inUse) {
			break
		}
		if ctx.Err() != nil {
			log.Fatalf("create table: %v", err)
		}
		log.Printf("waiting for DynamoDB: %v", err)
		time.Sleep(3 * time.Second)
	}

	describe := &dynamodb.DescribeTableInput{TableName: input.TableName}
	if err := dynamodb.NewTableExistsWaiter(db).Wait(ctx, describe, 2*time.Minute); err != nil {
		log.Fatalf("table not ready: %v", err)
	}

	// go-sync marks old items with ExpirationTime: let the database drop them.
	ttl, err := db.DescribeTimeToLive(ctx, &dynamodb.DescribeTimeToLiveInput{TableName: input.TableName})
	if err != nil {
		log.Fatalf("describe TTL: %v", err)
	}
	if d := ttl.TimeToLiveDescription; d == nil || d.TimeToLiveStatus != types.TimeToLiveStatusEnabled {
		_, err = db.UpdateTimeToLive(ctx, &dynamodb.UpdateTimeToLiveInput{
			TableName: input.TableName,
			TimeToLiveSpecification: &types.TimeToLiveSpecification{
				AttributeName: aws.String("ExpirationTime"),
				Enabled:       aws.Bool(true),
			},
		})
		if err != nil {
			log.Fatalf("enable TTL: %v", err)
		}
	}
	log.Printf("table %s ready", table)
}
