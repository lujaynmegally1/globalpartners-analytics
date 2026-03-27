End-to-End GlobalPartners Order Analytics Pipeline Architecture 
Component Breakdown & Design Rationale
Source - SQL server
Data Ingestion 
AWS Database Migration Service (DMS) 
Extracts data from RDS, and loads into S3.
Why DMS? 
Supports restartability and checkpoints
We will use DMS Checkpoints to capture only the changes (Inserts, Updates, Deletes) that occurred since the last successful run (24 hrs batch process) 
No custom ingestion code
Handles large tables efficiently
Future-Ready
While currently scheduled, this same task can be flipped to continuous mode if the business later requires sub-hour latency.
Built-in failure recovery
Encrypted replication storage via KMS 
Ideally, this design calls for "Full Load + Ongoing Replication" but with a custom stop point.  Every 24 hours, DMS would read the SQL Server transaction logs for the last 24 hours and write only those specific changes to the S3 Bronze layer. Once the "current time" is reached, the DMS task would pause and the replication instance can be stopped to save costs. Unlike full load every time, this would preserv the "journey" of the data (e.g., an order being modified or canceled) and significantly reduces the amount of data the downstream Glue jobs must process each day.
However, during the build, I discovered the source was running SQL Server Express Edition, which does not support the necessary distribution and logging for CDC. In a real-world production environment, I would advocate for upgrading the source to Standard or Enterprise to enable CDC for efficiency. But for this phase of the project, I pivoted to a Full Load strategy for now, and configured DMS to overwrite the 'Bronze' layer daily. 
Load & Transform 
AWS S3 Bronze 
Retain raw data for possible downstream needs
Why S3? 
Industry standard data lake storage
Decouples compute from storage
Extremely cost-efficient
Supports schema evolution and late-arriving data
Integrates natively with Glue & Athena
All data is stored in Parquet and encrypted using SSE-KMS.
AWS Glue Job 1 (PySpark)
Bronze/Raw → Silver
Performs foundational, deterministic transformations to create clean, queryable datasets.
Why Glue?
Fully serverless Spark (no cluster management)
Required by project (PySpark-only constraint)
Scales automatically for large datasets
Native integration with S3, DMS, Athena
Supports job bookmarks, retries, and fault tolerance
Responsibilities / Logic:
Merge/Upsert logic using PySpark for CDC 
Schema enforcement & type casting
Deduplication (idempotent transformations)
Null handling & default values
Timestamp standardization & timezone normalization
Join order_items and order_item_options
Calculate line-level revenue
Normalize dates and keys
Partition by order_date 
Glue Compactor: final step in Glue job that coalesces daily delta files in s3 bronze into larger Parquet blocks to prevent the "Small File Problem" that can slow down Athena querying.
AWS S3 Silver 
Holds cleaned data
Why Silver layer?
Separates cleaning logic from business logic
Enables reuse by multiple downstream consumers
Reduces recomputation when business logic changes
Improves debuggability and observability
Acts as a stable contract for analytics teams
AWS Glue Job 2 (PySpark) 
Silver → Gold
Applies business logic, metrics, and aggregations optimized for analytics and dashboards
Why 2 Glue Jobs?
Separation of concerns (data correctness vs business logic)
Enables faster iteration on metrics without reprocessing raw data
Reduces blast radius when business logic changes
Improves pipeline reliability and recovery
Aligns with industry-standard medallion architecture
Responsibilities / Logic:
Revenue aggregation
Customer Lifetime Value (CLV) computation
RFM segmentation
Churn indicators
Time-based aggregations
Etc 
Dashboard-ready fact and dimension tables
AWS S3 Gold
Stores curated, business-ready datasets for dashboard 
Why S3 Gold?
Cost-efficient storage for analytics outputs
Supports Athena and Redshift Spectrum(possible later addition) 
Decouples dashboard compute from transformation compute
Enables future warehouse ingestion if needed
Orchestration
Amazon EventBridge
Schedule daily pipeline execution
Serverless cron
Decouples scheduling from workflow
Triggers DMS 
AWS Step Functions
Orchestrate the full pipeline
Triggered by DMS replication task stopping 
Manage dependencies and retries
Why Step Functions? 
State tracking
Built-in retries
Failure isolation
Visual execution graph
DLQ integration
Workflow: 
Ingestion validation (DMS → S3 Bronze) 
Glue Job (S3 Bronze → S3 Gold) 
Glue Crawler trigger 
Data quality checks
Success notification (SNS) 
Failure → DLQ
Error Handling
Amazon SQS (DLQ)
Capture failed files, partitions, or jobs
Why DLQ?
Prevents total pipeline failure
Enables reprocessing
Preserves data integrity
DLQ Scenarios
Corrupt input files
Schema mismatch
Transformation errors
Query Layer 
Glue Crawler & Catalog 
Automatic Schema Discovery
Why? 
Reliability - If we ever change Glue job code to add a new column (like a new KPI), the Crawler will detect it and update Athena automatically, unlike manual schema writing in SQL DDL. 
Partition management - The Crawler automatically finds new date folders and adds them to the metadata.
Data Consistency - It ensures that the AWS Glue Data Catalog is the single source of truth for your entire AWS account. This means the data is visible not just in Athena, but also in SageMaker, QuickSight, and other Glue jobs.
AWS Athena
Query Gold tables directly from S3
Using Athena as a "SQL engine" on top of S3. Streamlit will send a SQL query to Athena, Athena does the heavy lifting in the AWS cloud, and it sends only the small, resulting table back to Streamlit. This keeps the dashboard fast.
Why Athena? 
Serverless
No infrastructure to manage
SQL interface
Scales automatically
Encrypted query results
Chosen instead of Redshift because:
Low concurrency
Batch analytics
Cost efficiency
No strict sub-second SLA
Redshift is positioned as a future optimization, easily later intergratable into this architecture after Athena, as it is better when there are hundreds of concurrent users, need sub-second query SLAs, and heavy star-schema joins. 
Target / Serving 
Streamlit on AWS ECS (Fargate) 
Interactive dashboards for stakeholders
Why? 
Python-native
Fast iteration
Production-deployable
Full control over layout, CSS, and interactive widgets.
Can perform complex logic in python, rather than standard charts in AWA Quicksight. 
Scales with ECS
Secure HTTPS via ALB
Connects to Athena for querying
Security & Encryption Strategy
RDS: KMS + TLS
DMS: KMS + TLS
S3: SSE-KMS
Glue: Inherits S3 encryption
Athena: Encrypted query outputs
Step Functions: Encrypted state
CloudWatch Logs: KMS
Streamlit: HTTPS
Encryption is explicitly enabled, not assumed.
CI/CD Strategy
GitHub + GitHub Actions
All PySpark, Streamlit, and configs stored in GitHub
Glue Job 1 & 2 stored in a Glue Scripts S3 Bucket 
Stremlit dashboard code stored in Docker Container in AWS ECR
Workflow
Local IDE → GitHub → CI Tests → Deploy to AWS
Why? 
Version control
Rollbacks
Code review
Auditability
Industry standard
Glue scripts are deployed from GitHub to S3 and referenced by Glue jobs.

Post build 
Ended up opting for full load , no cdc due to constraints with database being ‘express’ which doesnt support cdc like standard or developer level, which would be too costly to setup. 
