# Pipeline Setup Guide

> Back to [README](../README.md)

This guide walks through the full setup of the GlobalPartners AWS data pipeline from scratch — every resource, configuration, and credential needed to reproduce the environment.

---

## Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| AWS CLI | v2+ | Deploying and managing AWS resources |
| Python | 3.11+ | Dashboard development and local testing |
| Docker Desktop | Latest | Building and pushing container images |
| Azure Data Studio | Latest | Loading CSV files into SQL Server |
| Git | Any | Cloning repos and triggering CI/CD |

AWS permissions required: RDS, DMS, S3, Glue, Athena, Step Functions, ECS, ECR, EventBridge, KMS, IAM, CloudWatch

---

## Step 1 — Source Database (AWS RDS)

### 1.1 Create RDS Instance

1. Go to AWS RDS → Create database
2. Engine: Microsoft SQL Server
3. Edition: Standard or higher (Express does not support CDC — see [Project Log](project-log.md))
4. DB identifier: `globalpartners`
5. Enable Publicly accessible (required for Azure Data Studio connection)
6. Note the endpoint, port (1433), master username and password

### 1.2 Verify Recovery Mode

Connect via Azure Data Studio and run:

```sql
SELECT name, recovery_model_desc
FROM sys.databases
WHERE name = 'globalpartners';
-- Must return: FULL
```

If not FULL, run:

```sql
ALTER DATABASE globalpartners SET RECOVERY FULL;
```

### 1.3 Load Source Data

In Azure Data Studio, connect to the RDS endpoint and import each CSV:

1. Right-click database → Import Wizard
2. Upload `order_items.csv` → table `dbo.order_items`
3. Upload `order_item_options.csv` → table `dbo.order_item_options`
4. Upload `date_dim.csv` → table `dbo.date_dim`

Note: `date_dim.date_key` imports as varchar with format `dd-MM-yyyy`. This is handled in Glue Job 1 — do not attempt to fix it at the source.

---

## Step 2 — KMS Encryption Key

### 2.1 Create the Key

1. Go to AWS KMS → Create key
2. Type: Symmetric
3. Usage: Encrypt and decrypt
4. Alias: `globalpartners-datalake-kms`
5. Save the Key ARN — needed for S3, DMS, and Glue configurations

### 2.2 Grant Key Access

Add the following principals to the key policy as the project progresses:

- `globalpartners-dms-role` (added in Step 3)
- `globalpartners-gluejob-role` (added in Step 5)
- Athena service principal
- CloudWatch Logs service principal

---

## Step 3 — IAM Roles

### 3.1 DMS Role — `globalpartners-dms-role`

Managed policies to attach:

- `AmazonDMSCloudWatchLogsRole`
- `AmazonDMSVPCManagementRole`
- `AmazonS3FullAccess`

Inline policy for KMS:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "kms:Encrypt",
        "kms:Decrypt",
        "kms:GenerateDataKey",
        "kms:DescribeKey"
      ],
      "Resource": "*"
    }
  ]
}
```

### 3.2 Glue Role — `globalpartners-gluejob-role`

Managed policies to attach:

- `AWSGlueServiceRole`
- `AmazonS3FullAccess`
- `AmazonAthenaFullAccess`

Add the same KMS inline policy as above. Also update the KMS key policy to include this role as a key user.

### 3.3 ECS Task Role

Create a task execution role for ECS with:

- `AmazonECSTaskExecutionRolePolicy`
- `AmazonAthenaFullAccess`
- S3 read access to the Gold bucket

---

## Step 4 — S3 Buckets

Create four buckets, all in `us-east-1`:

| Bucket | Purpose | Encryption |
|---|---|---|
| `globalpartners-bronze` | Raw DMS output | SSE-KMS |
| `globalpartners-silver` | Cleaned Glue output | SSE-KMS |
| `globalpartners-gold` | Business metric tables | SSE-KMS |
| `globalpartners-glue-assets` | Glue script storage for CI/CD | SSE-KMS |

For each bucket: Properties → Default encryption → SSE-KMS → select `globalpartners-datalake-kms`.

---

## Step 5 — AWS DMS

### 5.1 Replication Instance

1. AWS DMS → Replication instances → Create
2. Name: `globalpartners-dms-replication`
3. Instance class: `dms.t3.medium`
4. Storage: 50 GB
5. VPC: Same VPC as RDS

### 5.2 Source Endpoint (SQL Server)

1. Endpoints → Create endpoint → Source
2. Engine: Microsoft SQL Server
3. Server name: your RDS endpoint
4. Port: `1433`
5. Database: `globalpartners`
6. Username/Password: your RDS credentials
7. Test connection before saving

### 5.3 Target Endpoint (S3 Bronze)

1. Endpoints → Create endpoint → Target
2. Name: `s3-bronze-globalpartners`
3. Engine: Amazon S3
4. S3 bucket: `globalpartners-bronze`

Extra connection attributes:

```
compressionType=NONE;
EnableStatistics=true;
DatePartitionEnabled=true;
dataFormat=parquet;
parquetVersion=parquet-1-0;
DatePartitionSequence=YYYYMMDD;
DatePartitionDelimiter=SLASH;
encryptionMode=SSE_KMS;
serverSideEncryptionKmsKeyId=arn:aws:kms:us-east-1:YOUR_ACCOUNT_ID:alias/globalpartners-datalake-kms
```

### 5.4 Replication Task

1. Tasks → Create task
2. Name: `globalpartners-sqlserver-to-s3-bronze`
3. Replication instance: `globalpartners-dms-replication`
4. Source/Target: your endpoints above
5. Migration type: Full load only (not Full load + CDC — see [Project Log](project-log.md))
6. Table mappings: Include `dbo.order_items`, `dbo.order_item_options`, `dbo.date_dim`

Task settings JSON:

```json
{
  "TargetMetadata": {
    "TaskRecoveryTableEnabled": true,
    "BatchApplyEnabled": true
  },
  "FullLoadSettings": {
    "TargetTablePrepMode": "DO_NOTHING"
  },
  "ErrorBehavior": {
    "FailOnNoTablesCaptured": false
  },
  "Logging": {
    "EnableLogging": true,
    "EnableLogContext": true
  }
}
```

---

## Step 6 — AWS Glue Jobs

### 6.1 Create Glue Job 1

1. AWS Glue → ETL Jobs → Create job
2. Name: `globalpartners-gluejob-1`
3. Role: `globalpartners-gluejob-role`
4. Type: Spark
5. Glue version: 4.0 (Spark 3.3, Python 3)
6. Script path: `s3://globalpartners-glue-assets/scripts/glue-job-1.py`
7. Number of workers: 2 (G.1X)

Upload [scripts/glue-job-1.py](../scripts/glue-job-1.py) to `s3://globalpartners-glue-assets/scripts/` before running.

### 6.2 Create Glue Job 2

Same configuration as above:

- Name: `globalpartners-gluejob-2-gold`
- Script path: `s3://globalpartners-glue-assets/scripts/glue-job-2.py`

Upload [scripts/glue-job-2.py](../scripts/glue-job-2.py) to `s3://globalpartners-glue-assets/scripts/` before running.

### 6.3 Create Glue Crawler

1. Glue → Crawlers → Create crawler
2. Name: `globalpartners-gold-crawler`
3. Data source: S3 → `s3://globalpartners-gold/`
4. IAM role: `globalpartners-gluejob-role`
5. Target database: `globalpartners_gold_db` (create if it does not exist)
6. Schedule: On demand (Step Functions triggers it)

---

## Step 7 — AWS Athena

### 7.1 Configure Query Results Location

1. Athena → Settings → Query result location
2. Set to: `s3://globalpartners-gold/athena-results/`
3. Enable SSE-KMS encryption for query results

### 7.2 Validate Tables After First Run

```sql
SELECT * FROM globalpartners_gold_db.customer_intelligence LIMIT 10;
SELECT * FROM globalpartners_gold_db.location_sales_trends LIMIT 10;
SELECT * FROM globalpartners_gold_db.discount_effectiveness;
```

---

## Step 8 — Streamlit Dashboard (Local)

[Build using these docs](../streamlit-dashboard-app/)

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app uses `awswrangler` to query Athena. Locally it uses your AWS CLI default profile. On ECS it uses the task IAM role — no code change needed between environments.

---

## Step 9 — Docker and ECR Deployment

### 9.1 Create ECR Repository

1. ECR → Create repository
2. Name: `global-partners-dashboard`
3. Enable image scanning

### 9.2 Build and Push Image

```bash
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin YOUR_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com

docker build --platform linux/amd64 -t global-partners-dashboard .

docker tag global-partners-dashboard:latest \
  YOUR_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/global-partners-dashboard:latest

docker push \
  YOUR_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/global-partners-dashboard:latest
```

Important: `--platform linux/amd64` is required when building on Apple Silicon (M1/M2). Fargate runs on amd64 — an arm64 image will fail at runtime.

### 9.3 ECS Fargate Deployment

1. ECS → Clusters → Create cluster (Fargate)
2. Task Definitions → Create — attach ECR image URI, assign task role
3. Services → Create — link cluster and task definition
4. Add the ECS service to an Application Load Balancer target group for a stable public URL

### 9.4 Cost Management

When not in use, pause Fargate to stop charges:

```
ECS → Your Cluster → Your Service → Update → Desired tasks: 0
```

Set back to 1 to resume.

---

## Step 10 — Step Functions Orchestration

### 10.1 Create the State Machine

1. Step Functions → State machines → Create
2. Type: Standard
3. Paste the state machine definition below

```json
{
  "StartAt": "Run Glue Job 1",
  "States": {
    "Run Glue Job 1": {
      "Type": "Task",
      "Resource": "arn:aws:states:::glue:startJobRun.sync",
      "Parameters": { "JobName": "globalpartners-gluejob-1" },
      "Catch": [{ "ErrorEquals": ["States.ALL"], "Next": "Pipeline Failed" }],
      "Next": "Run Glue Job 2"
    },
    "Run Glue Job 2": {
      "Type": "Task",
      "Resource": "arn:aws:states:::glue:startJobRun.sync",
      "Parameters": { "JobName": "globalpartners-gluejob-2-gold" },
      "Catch": [{ "ErrorEquals": ["States.ALL"], "Next": "Pipeline Failed" }],
      "Next": "Start Gold Crawler"
    },
    "Start Gold Crawler": {
      "Type": "Task",
      "Parameters": { "Name": "globalpartners-gold-crawler" },
      "Resource": "arn:aws:states:::aws-sdk:glue:startCrawler",
      "Catch": [{ "ErrorEquals": ["States.ALL"], "Next": "Pipeline Failed" }],
      "Next": "Wait For Crawler"
    },
    "Wait For Crawler": {
      "Type": "Wait",
      "Seconds": 60,
      "Next": "Check Crawler Status"
    },
    "Check Crawler Status": {
      "Type": "Task",
      "Parameters": { "Name": "globalpartners-gold-crawler" },
      "Resource": "arn:aws:states:::aws-sdk:glue:getCrawler",
      "Catch": [{ "ErrorEquals": ["States.ALL"], "Next": "Pipeline Failed" }],
      "Next": "Is Crawler Finished?"
    },
    "Is Crawler Finished?": {
      "Type": "Choice",
      "Choices": [
        {
          "Variable": "$.Crawler.State",
          "StringEquals": "RUNNING",
          "Next": "Wait For Crawler"
        },
        {
          "Variable": "$.Crawler.LastCrawl.Status",
          "StringEquals": "FAILED",
          "Next": "Pipeline Failed"
        }
      ],
      "Default": "Finished"
    },
    "Pipeline Failed": {
      "Type": "Fail",
      "Error": "ETL_Pipeline_Failure",
      "Cause": "A step in the pipeline failed. Check CloudWatch logs for details."
    },
    "Finished": {
      "Type": "Succeed"
    }
  }
}
```

### 10.2 Step Functions IAM Permissions

The execution role needs:

- `glue:StartJobRun`
- `glue:GetJobRun`
- `glue:StartCrawler`
- `glue:GetCrawler`
- `states:StartExecution`

---

## Step 11 — EventBridge Schedules

### 11.1 Daily DMS Trigger

1. EventBridge → Schedules → Create schedule
2. Name: `globalpartners-dms-daily-trigger`
3. Schedule: `cron(0 7 * * ? *)` — 7:00 AM UTC daily
4. Target: AWS DMS → startReplicationTask
5. Input:

```json
{
  "ReplicationTaskArn": "YOUR_DMS_TASK_ARN",
  "StartReplicationTaskType": "reload-target"
}
```

### 11.2 DMS Stop → Step Functions Trigger

1. EventBridge → Rules → Create rule
2. Name: `globalpartners-dms-stop-trigger`
3. Event pattern:

```json
{
  "source": ["aws.dms"],
  "detail-type": ["DMS Replication Task State Change"],
  "resources": ["YOUR_DMS_TASK_ARN"],
  "detail": {
    "type": ["REPLICATION_TASK"],
    "eventType": ["REPLICATION_TASK_STOPPED"]
  }
}
```

4. Target: your Step Functions state machine

---

## Step 12 — GitHub Actions CI/CD

### 12.1 Add GitHub Secrets

In both repos (`global-partners-dashboard` and `global-partners-etl`), go to Settings → Secrets → Actions and add:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

### 12.2 Glue CI/CD Workflow

File: [Glue ETL workflow yml document](../cicd/deploy-glue.yml )

Triggers on push to `main` when files in `scripts/**` change. Syncs scripts to S3 and runs `aws glue update-job` for both jobs.

### 12.3 Dashboard CI/CD Workflow

File: [ECS Dashboard workflow yml document](../cicd/deploy-ecs.yml)

Triggers on push to `main`. Builds Docker image, pushes to ECR, updates ECS task definition, waits for service stability.

---

## Full Pipeline Flow (End-to-End)

```
EventBridge (7 AM cron)
    → DMS Full Load (SQL Server → S3 Bronze)
        → EventBridge REPLICATION_TASK_STOPPED event
            → Step Functions State Machine
                → Glue Job 1 (Bronze → Silver)
                → Glue Job 2 (Silver → Gold)
                → Glue Crawler (update Athena catalog)
                    → Pipeline FINISHED
                        → Athena queries Gold tables
                            → Streamlit Dashboard (ECS Fargate via ALB)
```
