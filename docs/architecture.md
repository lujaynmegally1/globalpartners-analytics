# End-to-End GlobalPartners Order Analytics Pipeline Architecture

![Architecture Diagram](architecture-diagram.png)

---

## 📌 Table of Contents
* [Component Breakdown & Design Rationale](#component-breakdown--design-rationale)
* [Data Ingestion (DMS)](#data-ingestion)
* [Load & Transform (S3 & Glue)](#load--transform)
* [Orchestration (Step Functions)](#orchestration)
* [Query & Serving Layer (Athena & Streamlit)](#query-layer)
* [Security & CI/CD](#security--encryption-strategy)

---

# Component Breakdown & Design Rationale

## 🗄️ Source - SQL Server
The primary transactional database hosting order and customer data.

---

##  Data Ingestion

### AWS Database Migration Service (DMS)
Extracts data from RDS and loads into S3.

**Why DMS?**
* **Reliability:** Supports restartability and checkpoints.
* **Efficiency:** Uses DMS Checkpoints to capture only changes (Inserts, Updates, Deletes) occurring since the last 24-hour batch process.
* **Low Code:** No custom ingestion code required.
* **Scalability:** Handles large tables efficiently.

**Future-Ready:**
While currently scheduled, this same task can be flipped to continuous mode if the business later requires sub-hour latency.

**Built-in Features:**
* Built-in failure recovery.
* Encrypted replication storage via **AWS KMS**.

### Ideal Design (Planned)
The architectural intent is **"Full Load + Ongoing Replication"** with a custom stop point:
1. Every 24 hours, DMS reads SQL Server transaction logs.
2. Writes only those changes to the **S3 Bronze** layer.
3. Pauses once the "current time" is reached.
4. Replication instance stops to save costs.

**Benefits:**
* Preserves the *journey* of the data (e.g., order modifications, cancellations).
* Reduces downstream processing load.

> [!IMPORTANT]
> **Constraint & Pivot:** > * SQL Server Express Edition does not support **CDC (Change Data Capture)**.
> * In production, upgrading to Standard/Enterprise is recommended.
> * **Project Adjustment:** Pivoted to **Full Load strategy**, configuring DMS to overwrite Bronze daily.

---

##  Load & Transform

### AWS S3 Bronze
Stores raw data for downstream use.

**Why S3?**
* **Industry Standard:** Decouples compute from storage.
* **Cost:** Extremely cost-efficient.
* **Flexibility:** Supports schema evolution and late-arriving data.
* **Storage Format:** Parquet encrypted using **SSE-KMS**.

---

### AWS Glue Job 1 (PySpark)
**Bronze → Silver**
Performs foundational transformations to create clean datasets.

**Why Glue?**
* **Serverless:** Fully serverless Spark with no cluster management.
* **Constraint:** Meets the PySpark-only project requirement.
* **Resilience:** Supports retries, bookmarks, and fault tolerance.

**Responsibilities / Logic:**
* Merge/Upsert logic using PySpark for CDC.
* Schema enforcement & type casting.
* Deduplication (idempotent transformations).
* Null handling & default values.
* Timestamp standardization & timezone normalization.
* Join `order_items` and `order_item_options`.
* Partitioning by `order_date`.

**Glue Compactor:**
A final step that coalesces small files into larger Parquet blocks to prevent the **"Small File Problem"** and improve Athena performance.

---

### AWS S3 Silver
Holds cleaned, structured data.

**Why Silver Layer?**
* Separates cleaning logic from business logic.
* Enables reuse across consumers and reduces recomputation.
* Acts as a stable contract for analytics.

---

### AWS Glue Job 2 (PySpark)
**Silver → Gold**
Applies business logic and aggregations.

**Why Two Glue Jobs?**
* **Separation of Concerns:** Faster iteration on business logic with a reduced blast radius.
* **Architecture:** Aligns with the Medallion (Lakehouse) architecture.

**Responsibilities / Logic:**
* Revenue aggregation.
* **Customer Lifetime Value (CLV)** computation.
* **RFM segmentation** and churn indicators.
* Dashboard-ready fact and dimension tables.

---

### AWS S3 Gold
Stores curated, analytics-ready datasets.
* **Purpose:** Cost-efficient storage for Athena and Redshift Spectrum.
* **Future-Proof:** Enables future warehouse ingestion if needed.

---

##  Orchestration

### Amazon EventBridge
* Schedules daily pipeline execution via serverless cron.
* Triggers DMS tasks.

### AWS Step Functions
Orchestrates the end-to-end state machine.

**Why Step Functions?**
* State tracking and built-in retries.
* Visual execution graph for debugging.
* **DLQ (Dead Letter Queue)** integration.

**Workflow:**
1. Ingestion validation (DMS → S3 Bronze).
2. Glue Jobs (Bronze → Silver → Gold).
3. Glue Crawler trigger.
4. Data quality checks.
5. Success notification (SNS) or Failure → DLQ.

---

##  Error Handling

### Amazon SQS (DLQ)
Captures failed jobs and data to prevent total pipeline failure and enable reprocessing.

**DLQ Scenarios:**
* Corrupt input files.
* Schema mismatch.
* Transformation errors.

---

##  Query Layer

### Glue Crawler & Data Catalog
* **Why:** Automatically detects schema changes and manages partitions to ensure consistency across AWS services.

### AWS Athena
Queries the Gold layer directly from S3.

**Why Athena?**
* **Serverless:** No infrastructure to manage.
* **SQL Interface:** Uses standard SQL for the Streamlit dashboard.
* **Cost:** Pay-per-query model is ideal for batch analytics.

**Why Not Redshift?**
* Current use case has low concurrency and batch needs.
* Athena provides higher cost efficiency without the need for sub-second SLAs.

---

##  Target / Serving Layer

### Streamlit on AWS ECS (Fargate)
**Why?**
* **Python-native:** Fast iteration for data scientists/engineers.
* **Scalable:** Deployed via Docker on ECS Fargate.
* **Secure:** Accessible via ALB (HTTPS).

---

##  Security & Encryption Strategy

* **RDS/DMS:** KMS + TLS
* **S3:** SSE-KMS
* **Glue/Athena:** Inherited S3 encryption + Encrypted query outputs
* **Streamlit:** HTTPS termination

*Note: Encryption is explicitly enabled across all layers, not assumed.*

---

##  CI/CD Strategy

**Tools:** GitHub, GitHub Actions, AWS ECR.

**Workflow:**
1. **Local IDE:** Development of PySpark scripts and Streamlit app.
2. **GitHub:** Version control and code review.
3. **CI/CD:** GitHub Actions runs tests and deploys Glue scripts to S3 and Streamlit to Docker/ECR.

---

##  Post-Build Notes
* Final implementation uses **Full Load (no CDC)** due to SQL Server Express limitations.
* Upgrading to Standard/Enterprise would enable CDC in production.
* Current solution overwrites Bronze daily as a functional tradeoff for the project scope.
