# End-to-End GlobalPartners Order Analytics Pipeline Architecture
---
# Component Breakdown & Design Rationale

## Source - SQL Server

---

## Data Ingestion

### AWS Database Migration Service (DMS)

Extracts data from RDS and loads into S3.

### Why DMS?
- Supports restartability and checkpoints  
- We will use DMS Checkpoints to capture only the changes (Inserts, Updates, Deletes) that occurred since the last successful run (24 hrs batch process)  
- No custom ingestion code  
- Handles large tables efficiently  

### Future-Ready
- While currently scheduled, this same task can be flipped to continuous mode if the business later requires sub-hour latency  

### Built-in Features
- Built-in failure recovery  
- Encrypted replication storage via KMS  

### Ideal Design (Planned)
This design calls for **"Full Load + Ongoing Replication" with a custom stop point**:

- Every 24 hours, DMS reads SQL Server transaction logs  
- Writes only those changes to the S3 Bronze layer  
- Pauses once the "current time" is reached  
- Replication instance stops to save costs  

**Benefits:**
- Preserves the *journey* of the data (e.g., order modifications, cancellations)  
- Reduces downstream processing load  

### Constraint & Pivot
- SQL Server Express Edition does not support CDC  
- In production, upgrading to Standard/Enterprise would be recommended  
- For this project:
  - Pivoted to **Full Load strategy**
  - Configured DMS to overwrite Bronze daily  

---

## Load & Transform

### AWS S3 Bronze

Stores raw data for downstream use.

### Why S3?
- Industry standard data lake storage  
- Decouples compute from storage  
- Extremely cost-efficient  
- Supports schema evolution and late-arriving data  
- Native integration with Glue & Athena  

**Storage Format:**
- Parquet  
- Encrypted using SSE-KMS  

---

## AWS Glue Job 1 (PySpark)

### Bronze → Silver

Performs foundational transformations to create clean datasets.

### Why Glue?
- Fully serverless Spark (no cluster management)  
- Required by project (PySpark-only constraint)  
- Auto-scales for large datasets  
- Native AWS integration  
- Supports retries, bookmarks, and fault tolerance  

### Responsibilities / Logic
- Merge/Upsert logic using PySpark for CDC  
- Schema enforcement & type casting  
- Deduplication (idempotent transformations)  
- Null handling & default values  
- Timestamp standardization & timezone normalization  
- Join `order_items` and `order_item_options`  
- Calculate line-level revenue  
- Normalize dates and keys  
- Partition by `order_date`  

### Glue Compactor
Final step that:
- Coalesces small files into larger Parquet blocks  
- Prevents the "Small File Problem"  
- Improves Athena query performance  

---

## AWS S3 Silver

Holds cleaned, structured data.

### Why Silver Layer?
- Separates cleaning logic from business logic  
- Enables reuse across consumers  
- Reduces recomputation  
- Improves debuggability and observability  
- Acts as a stable contract for analytics  

---

## AWS Glue Job 2 (PySpark)

### Silver → Gold

Applies business logic and aggregations.

### Why Two Glue Jobs?
- Separation of concerns  
- Faster iteration on business logic  
- Reduced blast radius  
- Improved reliability  
- Aligns with medallion architecture  

### Responsibilities / Logic
- Revenue aggregation  
- Customer Lifetime Value (CLV) computation  
- RFM segmentation  
- Churn indicators  
- Time-based aggregations  
- Dashboard-ready fact and dimension tables  

---

## AWS S3 Gold

Stores curated, analytics-ready datasets.

### Why S3 Gold?
- Cost-efficient storage  
- Works with Athena and Redshift Spectrum  
- Decouples compute layers  
- Enables future warehouse ingestion  

---

## Orchestration

### Amazon EventBridge
- Schedules daily pipeline execution  
- Serverless cron  
- Triggers DMS  

### AWS Step Functions

Orchestrates the pipeline.

### Why Step Functions?
- State tracking  
- Built-in retries  
- Failure isolation  
- Visual execution graph  
- DLQ integration  

### Workflow
1. Ingestion validation (DMS → S3 Bronze)  
2. Glue Job (S3 Bronze → S3 Gold)  
3. Glue Crawler trigger  
4. Data quality checks  
5. Success notification (SNS)  
6. Failure → DLQ  

---

## Error Handling

### Amazon SQS (DLQ)

Captures failed jobs and data.

### Why DLQ?
- Prevents total pipeline failure  
- Enables reprocessing  
- Preserves data integrity  

### DLQ Scenarios
- Corrupt input files  
- Schema mismatch  
- Transformation errors  

---

## Query Layer

### Glue Crawler & Data Catalog

Provides automatic schema discovery.

### Why?
- Automatically detects schema changes  
- Manages partitions  
- Ensures consistency across AWS services  

---

### AWS Athena

Queries Gold layer directly from S3.

### How it Works
- Streamlit sends SQL queries to Athena  
- Athena processes queries  
- Returns small result sets to dashboard  

### Why Athena?
- Serverless  
- No infrastructure  
- SQL interface  
- Auto-scaling  
- Encrypted results  

### Why Not Redshift?
- Low concurrency use case  
- Batch analytics  
- Cost efficiency  
- No strict sub-second SLA  

### Future Consideration
Redshift can be added later for:
- High concurrency  
- Sub-second SLAs  
- Heavy joins  

---

## Target / Serving Layer

### Streamlit on AWS ECS (Fargate)

### Why?
- Python-native  
- Fast iteration  
- Production deployable  
- Full UI control  
- Supports complex Python logic  
- Scales with ECS  
- Secure via ALB (HTTPS)  
- Connects to Athena  

---

## Security & Encryption Strategy

- RDS: KMS + TLS  
- DMS: KMS + TLS  
- S3: SSE-KMS  
- Glue: Inherits S3 encryption  
- Athena: Encrypted query outputs  
- Step Functions: Encrypted state  
- CloudWatch Logs: KMS  
- Streamlit: HTTPS  

**Note:** Encryption is explicitly enabled, not assumed.

---

## CI/CD Strategy

### Tools
- GitHub  
- GitHub Actions  

### Setup
- PySpark + Streamlit + configs stored in GitHub  
- Glue scripts stored in S3  
- Streamlit deployed via Docker → AWS ECR  

### Workflow Local IDE → GitHub → CI Tests → Deploy to AWS

### Why?
- Version control  
- Rollbacks  
- Code review  
- Auditability  
- Industry standard  

---

## Post-Build Notes

- Final implementation uses **Full Load (no CDC)**  
- Constraint: SQL Server Express does not support CDC  
- Upgrading to Standard/Enterprise would enable CDC in production  
- Current solution overwrites Bronze daily as a tradeoff  

