# Data Model

> Back to [README](../README.md)

This document covers the full data model for the GlobalPartners analytics pipeline — source table schemas, the Silver layer sales fact, and all five Gold layer tables with column definitions, calculation logic, and the business question each table answers.

---

## Source Tables (SQL Server / Bronze Layer)

Three source tables land in S3 Bronze as raw Parquet files after each DMS full load.

### `order_items` (203,519 records)

Transactional-level data for each item in a customer's order.

| Column | Type | Description |
|---|---|---|
| `app_name` | String | Ordering platform or channel (e.g., mobile app, web) |
| `restaurant_id` | String | Unique identifier for the restaurant location |
| `creation_time_utc` | Timestamp | When the order was placed (UTC) |
| `order_id` | String | Unique identifier for the order |
| `user_id` | String | Unique identifier for the customer |
| `printed_card_number` | String | Tokenized loyalty card number |
| `is_loyalty` | Boolean | Loyalty membership flag (True/False) |
| `currency` | String | Transaction currency (e.g., USD) |
| `lineitem_id` | String | Unique identifier for this item within the order |
| `item_category` | String | Menu category (e.g., Beverage, Entree, Breakfast, Bowls) |
| `item_name` | String | Name of the menu item |
| `item_price` | Decimal | Unit price of the item |
| `item_quantity` | Integer | Quantity of this item ordered |

**Join key to options:** `(order_id, lineitem_id)`

---

### `order_item_options` (193,017 records)

Add-ons, customizations, or modifiers associated with order items.

| Column | Type | Description |
|---|---|---|
| `order_id` | String | Links to the parent order |
| `lineitem_id` | String | Links to the specific order item |
| `option_group_name` | String | Category of the option (e.g., Size, Toppings) |
| `option_name` | String | Selected customization (e.g., Extra Cheese) |
| `option_price` | Decimal | Price of the option (can be 0 for included options) |
| `option_quantity` | Integer | Number of times this option was added |

**Relationship:** Many-to-one with `order_items`. A single line item can have multiple options. Option prices are kept at row level in Silver to preserve granularity.

---

### `date_dim`

Calendar dimension table for time-based joins and aggregations.

| Column | Type | Description |
|---|---|---|
| `date_key` | Date | Full calendar date (source format: `dd-MM-yyyy`) |
| `day_of_week` | String | Day name (e.g., Monday) |
| `week` | Integer | Week number in the year |
| `month` | String | Month name (e.g., January) |
| `year` | Integer | Calendar year |
| `is_weekend` | Boolean | Whether the date falls on a weekend |
| `is_holiday` | Boolean | Whether the date is a recognized holiday |
| `holiday_name` | String | Name of the holiday if applicable |

**Known issue at ingestion:** `date_key` arrives as a varchar in SQL Server and requires explicit casting to `DateType` using format `dd-MM-yyyy` in Glue Job 1.

---

## Silver Layer

### `sales_fact` (partitioned by `order_date`)

The master sales table produced by Glue Job 1. Combines `order_items` and `order_item_options` at full row-level granularity.

**Key calculated columns:**

| Column | Calculation |
|---|---|
| `item_total` | `item_price × item_quantity` |
| `option_total` | `option_price × option_quantity` (0 if no option) |
| `total_line_item_revenue` | `item_total + option_total` |
| `order_date` | Extracted from `creation_time_utc` |

**Cleaning applied:**
- Deduplicated on `(order_id, lineitem_id)`
- `user_id` nulls filled with `"GUEST"`
- `item_price` and `option_price` cast to `DecimalType(12,2)`
- `creation_time_utc` cast to `TimestampType`
- `option_total` and `option_price` null-filled with `0` for items with no options

---

## Gold Layer Tables

All five Gold tables are written to `s3://globalpartners-gold/` and registered in the Glue Catalog as `globalpartners_gold_db`. They are queried by the Streamlit dashboard via Athena.

---

### 1. `customer_intelligence`

**Business question:** Who are our customers, how valuable are they, how engaged are they recently, and are they at risk of leaving?

One row per customer. The primary 360-degree customer profile table.

| Column | Type | Description |
|---|---|---|
| `user_id` | String | Unique customer identifier |
| `last_purchase_date` | Date | Date of the customer's most recent order |
| `current_clv` | Decimal | All-time total spend (lifetime revenue) |
| `frequency` | Integer | Count of distinct orders in the last 6 months |
| `monetary_value` | Decimal | Total spend in the last 6 months |
| `recency` | Integer | Days between `last_purchase_date` and the reference date (Feb 21, 2024) |
| `r_score` | Integer (1–5) | Recency percentile score — 5 = most recently active |
| `f_score` | Integer (1–5) | Frequency percentile score — 5 = most frequent |
| `m_score` | Integer (1–5) | Monetary percentile score (6-month spend) — 5 = highest spender |
| `clv_score` | Integer (1–5) | All-time CLV percentile score |
| `avg_gap_between_orders` | Decimal | Average days between consecutive orders (lifetime) |
| `avg_spend_change_pct` | Decimal | Average % change in spend between consecutive orders |
| `clv_tier` | String | `High` / `Medium` / `Low` based on all-time CLV percentile |
| `churn_status` | String | `At Risk` if recency > 45 days, otherwise `Active` |
| `segment` | String | Behavioral label — see segment logic below |

**CLV Tier logic (based on all-time spend):**

| Tier | Condition | Business Definition |
|---|---|---|
| High | `clv_score = 5` (top 20%) | Whales — highest lifetime value customers |
| Medium | `clv_score >= 2` (middle 60%) | Core base — consistent, moderate spenders |
| Low | `clv_score = 1` (bottom 20%) | Occasional — one-time or low-value purchasers |

**Segment logic (based on last 6 months of RFM):**

| Segment | Condition | Business Definition |
|---|---|---|
| VIP | `r_score >= 4` AND `f_score >= 4` AND `m_score >= 4` | Recent, frequent, high spenders |
| New Customer | `f_score <= 2` AND `r_score >= 4` | Recently active but low order history |
| Churn Risk | `recency > 45 days` | Inactive for over 45 days — needs re-engagement |
| Standard | All others | Mid-range RFM — reliable but not standout |

**Reference date:** Feb 21, 2024 (max order date in the dataset). Frequency and monetary are scoped to the **last 6 months** from this date to reflect recent behavior rather than all-time history across the dataset's 4-year span.

---

### 2. `customer_rolling_metrics`

**Business question:** How has each customer's cumulative spend grown over time?

One row per customer per order date. Used for CLV-over-time visualizations.

| Column | Type | Description |
|---|---|---|
| `user_id` | String | Unique customer identifier |
| `order_id` | String | Order identifier |
| `order_date` | Date | Date of the order |
| `order_revenue` | Decimal | Total revenue for this specific order |
| `is_loyalty` | Boolean | Loyalty membership flag at time of order |
| `running_clv` | Decimal | Cumulative total spend by this customer up to and including this date |

**Running CLV calculation:**
```
running_clv = SUM(order_revenue) OVER (
    PARTITION BY user_id
    ORDER BY order_date
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)
```

This produces a time-series view of each customer's CLV growth — when plotted, gaps between points indicate periods of inactivity.

---

### 3. `location_sales_trends`

**Business question:** How does revenue vary by location, menu category, and over time?

One row per `(order_date, restaurant_id, item_category)` combination. Partitioned by `order_date` for efficient Athena queries.

| Column | Type | Description |
|---|---|---|
| `order_date` | Date | Calendar date (partition key) |
| `restaurant_id` | String | Store location identifier |
| `item_category` | String | Menu category (e.g., Breakfast, Bowls, Salads, Beverages) |
| `daily_revenue` | Decimal | Total revenue for this category at this location on this date |
| `daily_order_count` | Integer | Count of unique orders at this location on this date |

**Used for:** Sales trend monitoring, location performance ranking, weekly/monthly aggregation in the dashboard (Streamlit groups this further using `pd.Grouper`).

---

### 4. `loyalty_roi_analysis`

**Business question:** Does loyalty membership drive more value per customer?

One row per loyalty group (`is_loyalty = True` / `False`).

| Column | Type | Description |
|---|---|---|
| `is_loyalty` | Boolean | Loyalty membership flag |
| `total_customers` | Integer | Count of unique customers in this group |
| `avg_order_value` | Decimal | Average total revenue per order for this group |
| `repeat_order_rate` | Decimal | Average number of orders per customer (`order_count / customer_count`) |
| `total_lifetime_value` | Decimal | Sum of all revenue generated by this group |

**Key insight from the data:** Non-members have a higher Average Order Value (driven by large one-off catering or group orders), while loyalty members have a higher repeat order rate — indicating the program builds habitual behavior rather than high-ticket transactions.

---

### 5. `discount_effectiveness`

**Business question:** Are discounts driving incremental revenue or just eroding margin?

One row per discount group (`is_discounted = 1` / `0`).

| Column | Type | Description |
|---|---|---|
| `is_discounted` | Integer | `1` if the order contained a discounted item, `0` otherwise |
| `order_count` | Integer | Volume of orders in this group |
| `total_revenue` | Decimal | Total revenue generated by this group |
| `avg_order_value` | Decimal | Average revenue per order in this group |

**Discount detection logic:**

The source data does not use negative `option_price` values to represent discounts (an initial assumption that proved incorrect). Instead, discount detection uses a dynamic comparison:

```python
# For each item/option, find the max price ever charged for that name
max_item_price_ever = MAX(item_price) OVER (PARTITION BY item_name)
max_option_price_ever = MAX(option_price) OVER (PARTITION BY option_name)

# Flag as discounted if price is 0 but the item/option has been charged elsewhere
is_true_discount = (item_price == 0 AND max_item_price_ever > 0)
                OR (option_price == 0 AND max_option_price_ever > 0)
```

An order is flagged as discounted if any line item within it is flagged. See [Project Log](project-log.md) for the full story on discovering and correcting this logic.

---

## Table Relationship Summary

```
order_items ──────────────────────────────────────────────────────┐
     │                                                             │
     │ (order_id, lineitem_id)                                     │
     ▼                                                             │
order_item_options                                                 │
     │                                                             │
     └──── JOIN ──── sales_fact (Silver) ─────────────────────────┘
                           │
              ┌────────────┼────────────────────┬──────────────────────┐
              ▼            ▼                    ▼                      ▼
  customer_intelligence  customer_rolling   location_sales_trends   loyalty_roi_analysis
                         _metrics
                                                                    discount_effectiveness
```
