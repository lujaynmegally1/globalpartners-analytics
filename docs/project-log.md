# Project Log

> Back to [README](../README.md)

A detailed chronological record of how this project was built — including every decision made, roadblock hit, and pivot taken. Written for coaches and reviewers who want to understand the reasoning behind the work, not just the final output.

---

## Phase 1 — Source Data Exploration

### What I did

Downloaded the three source CSV files and manually explored them before writing any code.

**Findings:**

| File | Records | Issues Found |
|---|---|---|
| `order_items` | 203,519 | Null values in `user_id` and some price columns |
| `order_item_options` | 193,017 | 2,299 duplicated rows |
| `date_dim` | — | Null values; `date_key` stored as `varchar` in `dd-MM-yyyy` format |

**Key join relationships identified:**
- `order_items` ↔ `order_item_options` via `(order_id, lineitem_id)`
- `order_items` ↔ `date_dim` via `creation_time_utc` date = `date_key`

**Many-to-one join risk flagged early:** `order_item_options` has multiple rows per `lineitem_id`. Joining naively without pre-aggregating would multiply rows and inflate revenue totals. Decided to keep options at full granularity in Silver and handle the revenue math at the column level (`option_price × option_quantity = option_total`), then sum at the order level in Gold.

---

## Phase 2 — Source Database Setup

### What I did

Created an AWS RDS instance running SQL Server and loaded the CSV files using Azure Data Studio.

- Database name: `globalpartners`
- Uploaded: `order_items`, `order_item_options`, `date_dim`
- Verified `date_key` type mismatch (`varchar` vs. expected `date`) — flagged for handling in Glue Job 1

**Recovery mode verification:**
```sql
SELECT name, recovery_model_desc
FROM sys.databases
WHERE name = 'globalpartners';
-- Required: FULL
```

This was a prerequisite for DMS — RDS must be in FULL recovery mode for DMS to read transaction logs.

---

## Phase 3 — AWS Infrastructure Setup

### KMS Encryption Key

Created a customer-managed KMS key with alias `globalpartners-datalake-kms`.

**Why not use AWS default encryption?**
AWS defaults to AWS-managed keys. For production data governance, a customer-managed key gives explicit control over who can encrypt and decrypt — and provides a clear audit trail. We explicitly granted access to the DMS role at this stage, with Glue, Athena, and CloudWatch added as needed later.

### S3 Buckets

Created `globalpartners-bronze` with SSE-KMS encryption. Silver and Gold buckets created later at each respective stage.

### IAM Role for DMS

Created `globalpartners-dms-role` with:
- `AmazonDMSCloudWatchLogsRole`
- `AmazonDMSVPCManagementRole`
- `AmazonS3FullAccess`
- Inline KMS policy for encrypt/decrypt/generate/describe

---

## Phase 4 — DMS Setup & First Major Roadblock

### What I did

Configured the DMS replication instance, source endpoint (SQL Server), and target endpoint (S3 Bronze).

**Target endpoint configuration:**
- Format: Parquet
- Compression: None
- Date partition: Enabled (`YYYYMMDD/SLASH` format)
- `EnableStatistics: true`

**Table mapping rules added:** Two metadata columns appended to every table to support future CDC logic:
- `op` — operation type (`I` = Insert, `U` = Update, `D` = Delete)
- `commit_ts` — commit timestamp

### 🚧 Roadblock 1: CDC Not Supported on SQL Server Express

Configured the DMS task as "Full Load + CDC" (the ideal production pattern). On test run, received:

```
Msg 22988 — This instance of SQL Server is the Express Edition (64-bit).
Change data capture is only available in Enterprise, Developer,
Enterprise Evaluation, and Standard editions.
```

**Options considered:**

| Option | Pros | Cons |
|---|---|---|
| Upgrade to Standard/Developer Edition | Enables CDC, ideal for production | Requires new RDS instance, data reload, endpoint reconfiguration — added cost |
| Pivot to Full Load only | Simple, works immediately, no cost | Overwrites Bronze daily, loses CDC "journey" data (modifications, cancellations) |

**Decision:** Pivot to Full Load. The dataset is static (not a live production system), and the business value of CDC didn't justify the additional cost for this phase. In a real production environment, Standard or Enterprise edition would be required and worth advocating for.

**Changes made:**
- Removed `op` and `commit_ts` columns from table mapping rules (not meaningful without CDC)
- Created a new DMS task configured as Full Load only
- Configured to overwrite S3 Bronze on each run

**Result:** Full Load completed successfully. Data landed in `globalpartners-bronze/dms/dbo/` partitioned by date.

---

## Phase 5 — Glue Job 1 (Bronze → Silver)

### What I did

Created the first Glue PySpark job to clean the raw Bronze data and produce Silver.

**IAM setup:** Created `globalpartners-gluejob-role` with S3 read/write access and KMS permissions. Initially hit a permissions failure on first run — the KMS key policy hadn't been updated to include the new Glue role. Added the Glue role to the KMS key policy.

**Schema issue with `order_item_options`:** Discovered that the column names in the Bronze Parquet files had uppercase names (`ORDER_ID`, `LINEITEM_ID`, etc.) rather than lowercase. Added explicit `withColumnRenamed()` calls to normalize to lowercase.

**Join architecture decision:**

The many-to-one relationship between `order_item_options` and `order_items` required a deliberate choice:
- **Option A:** Pre-aggregate option prices by `(order_id, lineitem_id)` before joining → cleaner, but loses individual option detail
- **Option B:** Left join at full row granularity, keeping every option row → preserves detail, allows future analysis at option level

Chose **Option B** to keep Silver as a detailed, reusable dataset. Revenue aggregation happens in Gold.

**Result:** Glue Job 1 ran successfully in ~5 minutes. Silver `sales_fact` partitioned by `order_date`.

> → [View Glue Job 1 Code](../scripts/glue-job-1.py)
---

## Phase 6 — Glue Job 2 (Silver → Gold)

### What I did

Built the second Glue job implementing all five business metric tables.

**Key design decisions:**

**RFM scope:** The dataset spans ~4 years. Using all-time data for Frequency and Monetary would dilute recent behavior signals. Scoped Frequency and Monetary to the **last 6 months** before the reference date (Feb 21, 2024 — the max order date). Recency and CLV tier remain all-time.

**CLV tier vs. RFM segment:** These are intentionally different:
- **CLV tier** (High/Medium/Low) is based on all-time spend — who has historically been most valuable
- **Segment** (VIP/New/Churn Risk/Standard) is based on recent 6-month RFM — who is most engaged *right now*

A customer can be "High CLV" but "Churn Risk" — meaning they were historically a top spender but have been inactive recently. This distinction is surfaced in the Churn Indicators dashboard.

**Result:** Glue Job 2 ran successfully in ~9 minutes.

> → [View Glue Job 2 Code](../scripts/glue-job-2.py)
---

## Phase 7 — Discount Effectiveness Roadblock

### 🚧 Roadblock 2: Incorrect Discount Detection Logic

After running Glue Job 2 and querying the `discount_effectiveness` table in Athena, the result showed only one row with `is_discounted = 0` — meaning zero discounts were detected in the entire dataset.

**Initial (incorrect) assumption:** Discounts would appear as negative `option_price` values in `order_item_options`.

**Investigation:** Queried the Silver table in Athena and confirmed there are no negative `option_price` values anywhere in the data.

**Root cause:** Discounts in this system are represented as items or options priced at `$0.00` — not negative values. A `$0` item that normally costs money is the discount signal.

**New logic designed:**

```python
# Find the max price ever charged for each item/option name across all orders
max_item_price_ever = MAX(item_price) OVER (PARTITION BY item_name)
max_option_price_ever = MAX(option_price) OVER (PARTITION BY option_name)

# Flag as discount if price is 0 but it has been charged elsewhere (not inherently free)
is_true_discount = (item_price == 0 AND max_item_price_ever > 0)
               OR (option_price == 0 AND max_option_price_ever > 0)
```

This distinguishes between items that are always free (not a discount) and items that are sometimes charged (a real discount when at $0).

**Glue Job 1 also updated** to ensure `option_price` was being passed through correctly to Silver before the discount logic could evaluate it.

**Result:** Re-ran Glue Job 2. Discount effectiveness table now correctly shows discounted vs. non-discounted orders.

---

## Phase 8 — Glue Crawler & Athena Validation

### What I did

Created the `globalpartners-gold-crawler` pointed at the Gold S3 bucket, connected to database `globalpartners_gold_db`.

**Ran crawler** — all five Gold tables registered in the Glue Catalog automatically.

**Validated in Athena** — spot-checked each table. Also discovered `is_loyalty` was being carried into `customer_rolling_metrics` unnecessarily (it's already in the loyalty analysis table). Left it in as it's useful for filtering rolling CLV by loyalty status on the dashboard — not a bug.

---

## Phase 9 — Streamlit Dashboard

### What I did

Developed the dashboard locally in Python, connecting to Athena via `awswrangler`. All six dashboard sections built and tested:

1. Customer Lifetime Value (CLV)
2. Customer Segmentation & Behavior
3. Churn Indicators
4. Sales Trends Monitoring
5. Top-Performing Locations
6. Loyalty Program Impact
7. Pricing & Discount Effectiveness

**Data type issue:** Athena returns some numeric columns as `object` dtype in pandas. Added explicit `pd.to_numeric(..., errors='coerce')` conversion with `.fillna(0)` for all numeric columns after load.

**Boolean handling:** The `is_loyalty` boolean from Athena can arrive as `True`/`False`, `'true'`/`'false'`, `1`/`0` depending on query path. Added explicit `.astype(str).str.lower()` normalization before mapping to display labels.

---

## Phase 10 — Containerization & ECS Deployment

### What I did

Dockerized the Streamlit app and deployed to AWS ECS Fargate.

**Dockerfile highlights:**
- Base image: `python:3.11-slim`
- Platform: `linux/amd64` (required for Fargate — M1/M2 Mac builds default to `arm64` which fails on Fargate)
- Healthcheck: `curl http://localhost:8501/_stcore/health`

**ECR push commands:**
```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com
docker build --platform linux/amd64 -t global-partners-dashboard .
docker tag global-partners-dashboard:latest <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/global-partners-dashboard:latest
docker push <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/global-partners-dashboard:latest
```

**Initial deployment issue:** Each new ECS task deployment generated a new public IP. Required manually checking the ECS service for the latest task IP after each push.

**Fix: Application Load Balancer (ALB)**

Added an ALB (`global-partners-tg` target group + load balancer) and linked it to the ECS service. The ALB provides a stable, persistent URL that automatically routes to whichever task is healthy:

```
http://global-partners-1443988300.us-east-1.elb.amazonaws.com/
```

After this change, GitHub Actions CI/CD deployments automatically shift traffic to the new task with zero downtime — no manual IP lookup needed.

---

## Phase 11 — CI/CD Setup

### Dashboard CI/CD (`global-partners-dashboard` repo)

GitHub Actions workflow triggers on push to `main`:
1. Configure AWS credentials
2. Log in to ECR
3. Build Docker image (`linux/amd64`)
4. Push with commit SHA as image tag
5. Render new ECS task definition with updated image
6. Deploy to ECS service and wait for stability

**Tested:** Made a UI change, pushed to GitHub, confirmed the change appeared at the ALB URL ~15 minutes later. ✅

### Glue CI/CD (`global-partners-etl` repo)

GitHub Actions workflow triggers on push to `main` when files in `scripts/**` change:
1. Configure AWS credentials
2. Sync `./scripts/` folder to `s3://globalpartners-glue-assets/scripts/`
3. Run `aws glue update-job` for Job 1 and Job 2 with new S3 script location

**Tested:** Added a comment to `glue-job-1.py`, pushed to GitHub, confirmed the updated script path appeared in the Glue console Job Details tab. ✅

---

## Phase 12 — Orchestration

### EventBridge Schedules

- **`globalpartners-dms-daily-trigger2`:** Fires at 7:00 AM daily, starts the DMS replication task (`reload-target`)
- **`globalpartners` event rule:** Listens for `REPLICATION_TASK_STOPPED` DMS event on the specific task ARN → triggers Step Functions state machine

### Step Functions State Machine

Built the orchestration state machine covering:

```
Run Glue Job 1 → Run Glue Job 2 → Start Gold Crawler → Poll Crawler Status → Finished
```

Each state has a `Catch` block routing failures to a `Pipeline Failed` terminal state. The Crawler poll uses a `Wait → GetCrawler → Choice` loop to handle variable crawl durations.

**End-to-end pipeline test:** Manually triggered the EventBridge DMS schedule → confirmed DMS ran → Step Functions triggered → both Glue jobs ran sequentially → Crawler completed → Athena tables updated. ✅

---

## Summary of Key Decisions & Pivots

| Decision | Why |
|---|---|
| Full Load instead of CDC | SQL Server Express Edition doesn't support CDC; cost of upgrading not justified for this phase |
| Two Glue Jobs instead of one | Separation of concerns — cleaning vs. business logic. Reduces blast radius on logic changes |
| Athena over Redshift | Low concurrency, batch analytics, pay-per-query fits the use case. Redshift positioned as future upgrade |
| Streamlit over QuickSight | Python-native, full layout control, no additional licensing cost |
| ALB added after initial deployment | Stable URL required for CI/CD to work without manual IP lookups |
| 6-month RFM window | Dataset spans 4 years — all-time F and M would dilute recent behavior signals |
| $0 price = discount logic | No negative prices in the data; discounts represented as $0 for normally-charged items |
