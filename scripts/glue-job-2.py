# Deployment Test: Feb 20th
import sys
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql import functions as F
from pyspark.sql.window import Window

# Initialize Glue Context
args = getResolvedOptions(sys.argv, ['JOB_NAME'])
sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args['JOB_NAME'], args)

# Paths
SILVER_PATH = "s3://globalpartners-silver/"
GOLD_PATH = "s3://globalpartners-gold/"

# Load Silver Data
# sales_fact contains merged order items and options from your previous job
df_sales = spark.read.parquet(f"{SILVER_PATH}sales_fact/")

# --- 1. PREPARATION: ORDER-LEVEL SUMMARY ---
# Aggregating to the order level to calculate inter-order gaps and spend changes
order_summary = df_sales.filter(F.col("user_id") != "GUEST").groupBy("user_id", "order_id", "order_date").agg(
    F.sum("total_line_item_revenue").alias("order_revenue"),
    F.max("is_loyalty").alias("is_loyalty")
)

# --- 2. ADVANCED BEHAVIORAL METRICS (Gaps & Spend Change) ---
window_user = Window.partitionBy("user_id").orderBy("order_date")

order_metrics = (order_summary
    .withColumn("prev_order_date", F.lag("order_date").over(window_user))
    .withColumn("prev_order_revenue", F.lag("order_revenue").over(window_user))
    .withColumn("days_since_prev_order", F.datediff(F.col("order_date"), F.col("prev_order_date")))
    .withColumn("spend_change_pct", 
        F.when(F.col("prev_order_revenue").isNotNull(), 
               ((F.col("order_revenue") - F.col("prev_order_revenue")) / F.col("prev_order_revenue")) * 100)
        .otherwise(0))
)

# --- 3. CUSTOMER INTELLIGENCE (RFM, SEGMENTS, CHURN) ---
reference_date = df_sales.select(F.max("order_date")).collect()[0][0]
six_months_ago = F.add_months(F.lit(reference_date), -6)

customer_base = (order_metrics
    .groupBy("user_id")
    .agg(
        F.max("order_date").alias("last_purchase_date"),
        
        # 1. Lifetime Metric: Current CLV (All time spend)
        F.sum("order_revenue").alias("current_clv"),
        
        # 2. Recent Metrics (Last 6 Months)
        F.count(F.when(F.col("order_date") >= six_months_ago, F.col("order_id"))).alias("frequency"),
        F.sum(F.when(F.col("order_date") >= six_months_ago, F.col("order_revenue"))).alias("monetary_value"),
        
        F.avg("days_since_prev_order").alias("avg_gap_between_orders"),
        F.avg("spend_change_pct").alias("avg_spend_change_pct")
    )
    .withColumn("recency", F.datediff(F.lit(reference_date), F.col("last_purchase_date")))
    .fillna({"frequency": 0, "monetary_value": 0, "current_clv": 0})
)

# RFM Scoring Windows
window_rec = Window.orderBy(F.col("recency").desc()) 
window_freq = Window.orderBy("frequency")
window_mon = Window.orderBy("monetary_value") # 6-month monetary score
window_clv = Window.orderBy("current_clv")   # All-time CLV score

customer_intelligence = (customer_base
    .withColumn("r_score", F.ntile(5).over(window_rec))
    .withColumn("f_score", F.ntile(5).over(window_freq))
    .withColumn("m_score", F.ntile(5).over(window_mon))
    .withColumn("clv_score", F.ntile(5).over(window_clv)) # New score for tiers
    
    # clv_tier is now based on ALL TIME spend (current_clv)
    .withColumn("clv_tier", 
        F.when(F.col("clv_score") == 5, "High")
        .when(F.col("clv_score") >= 2, "Medium")
        .otherwise("Low"))
    
    .withColumn("churn_status", F.when(F.col("recency") > 45, "At Risk").otherwise("Active"))
    
    # Segments are based on RECENT behavior (6-month RFM)
    .withColumn("segment", 
        F.when((F.col("r_score") >= 4) & (F.col("f_score") >= 4) & (F.col("m_score") >= 4), "VIP")
        .when((F.col("f_score") <= 2) & (F.col("r_score") >= 4), "New Customer")
        .when(F.col("recency") > 45, "Churn Risk")
        .otherwise("Standard"))
)
# --- 4. ROLLING CLV (For Historical Growth Charts) ---
window_rolling = Window.partitionBy("user_id").orderBy("order_date").rowsBetween(Window.unboundedPreceding, Window.currentRow)
df_rolling = order_summary.withColumn("running_clv", F.sum("order_revenue").over(window_rolling))

# --- 5. LOCATION SALES TRENDS ---
location_sales_trends = df_sales.groupBy("order_date", "restaurant_id", "item_category").agg(
    F.sum("total_line_item_revenue").alias("daily_revenue"),
    F.countDistinct("order_id").alias("daily_order_count")
)

# --- 6. LOYALTY ROI ANALYSIS ---
loyalty_roi_analysis = order_summary.groupBy("is_loyalty").agg(
    F.countDistinct("user_id").alias("total_customers"),
    F.avg("order_revenue").alias("avg_order_value"),
    (F.count("order_id") / F.countDistinct("user_id")).alias("repeat_order_rate"),
    F.sum("order_revenue").alias("total_lifetime_value")
)

# --- 7. DYNAMIC DISCOUNT EFFECTIVENESS (Item & Option Logic) ---
# Window to find max price ever charged for each Item and Option name
window_item_cat = Window.partitionBy("item_name")
window_option_cat = Window.partitionBy("option_name")

df_discount_logic = df_sales.withColumn(
    "max_item_price_ever", F.max("item_price").over(window_item_cat)
).withColumn(
    "max_option_price_ever", F.max("option_price").over(window_option_cat)
).withColumn(
    "is_true_discount",
    F.when(
        ((F.col("item_price") == 0) & (F.col("max_item_price_ever") > 0)) | 
        ((F.col("option_price") == 0) & (F.col("max_option_price_ever") > 0)), 
        1
    ).otherwise(0)
)

window_order_final = Window.partitionBy("order_id")
discount_effectiveness = (df_discount_logic
    .withColumn("order_has_discount", F.max("is_true_discount").over(window_order_final))
    .groupBy("order_has_discount")
    .agg(
        F.countDistinct("order_id").alias("order_count"),
        F.sum("total_line_item_revenue").alias("total_revenue"),
        F.avg("total_line_item_revenue").alias("avg_order_value")
    ).withColumnRenamed("order_has_discount", "is_discounted")
)


# --- WRITE TO GOLD ---
customer_intelligence.write.mode("overwrite").parquet(f"{GOLD_PATH}customer_intelligence/")
df_rolling.write.mode("overwrite").parquet(f"{GOLD_PATH}customer_rolling_metrics/")
location_sales_trends.write.mode("overwrite").partitionBy("order_date").parquet(f"{GOLD_PATH}location_sales_trends/")
loyalty_roi_analysis.write.mode("overwrite").parquet(f"{GOLD_PATH}loyalty_roi_analysis/")
discount_effectiveness.write.mode("overwrite").parquet(f"{GOLD_PATH}discount_effectiveness/")

job.commit()