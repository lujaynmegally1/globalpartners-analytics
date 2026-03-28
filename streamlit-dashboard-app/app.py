import streamlit as st
import pandas as pd
import awswrangler as wr
import plotly.express as px

st.set_page_config(page_title="GlobalPartners Analytics", layout="wide")

st.title("📊 GlobalPartners Order Analytics")
st.markdown("""
    *Automated daily refresh from AWS Athena @ 8:00 AM.* Use the sidebar to filter by segment and explore tailored metrics.
""")

# testing ci/cd 
st.divider()
st.header("testing - CI/CD should trigger and deploy this change to the dashboard")



# 1. Connection Logic
# Note: Locally, this uses your AWS CLI credentials. On ECS, it uses the Task Role.
DATABASE = "globalpartners_gold_db"

@st.cache_data(ttl=3600) # Caches data for 1 hour so it's snappy
def load_gold_data(table):
    return wr.athena.read_sql_query(sql=f"SELECT * FROM {table}", database=DATABASE)

# Load all tables
df_intel = load_gold_data("customer_intelligence")

# --- 0. Data Cleaning (Add this after load_gold_data) ---
# Convert Narwhals/Object types to standard numeric floats
numeric_cols = ['current_clv', 'monetary_value', 'frequency', 'recency', 'avg_gap_between_orders', 'avg_spend_change_pct']
for col in numeric_cols:
    if col in df_intel.columns:
        df_intel[col] = pd.to_numeric(df_intel[col], errors='coerce').fillna(0)


df_rolling = load_gold_data("customer_rolling_metrics")
df_trends = load_gold_data("location_sales_trends")
df_loyalty = load_gold_data("loyalty_roi_analysis")
df_discount = load_gold_data("discount_effectiveness")

# --- 1. Customer Lifetime Value (CLV) ---
st.header("1. Customer Lifetime Value (CLV)")

with st.expander("🔍 View Segmentation Definitions & Logic"):
    st.markdown("""
    We use **Percentile Ranking (NTILE)** based on a customer's **Lifetime Total Spend** to assign tiers. This ensures the tiers stay relevant even as the business grows.
    
    | Tier | Logic | Business Definition |
    | :--- | :--- | :--- |
    | **High** | Top 20% | **Whales:** The highest-value customers who drive the majority of revenue. |
    | **Medium** | Middle 60% | **Core Base:** Reliable customers with consistent, moderate lifetime spend. |
    | **Low** | Bottom 20% | **Occasional:** One-time or low-value purchasers. |
    
    *Note: Tiers are calculated based on **All-Time Spend**, while 'VIP' or 'Churn' status is based on activity within the **last 6 months**.*
    """)

# 1a. Average CLV by Segment (KPI Metrics)
st.subheader("Average CLV by Segment")

# Calculate the averages
avg_clv_high = df_intel[df_intel['clv_tier'] == 'High']['current_clv'].mean()
avg_clv_med = df_intel[df_intel['clv_tier'] == 'Medium']['current_clv'].mean()
avg_clv_low = df_intel[df_intel['clv_tier'] == 'Low']['current_clv'].mean()

# Create 3 columns for the metrics
col1, col2, col3 = st.columns(3)

with col1:
    st.metric(label="High Tier Avg", value=f"${avg_clv_high:,.2f}")

with col2:
    st.metric(label="Medium Tier Avg", value=f"${avg_clv_med:,.2f}")

with col3:
    st.metric(label="Low Tier Avg", value=f"${avg_clv_low:,.2f}")



# --- 1b. CLV Comparison by User ---
st.subheader("Individual Customer CLV Comparison")

# Add a slider to let the user choose how many top customers to view
top_n = st.slider("Select number of top customers to compare", 5, 100, 20)

# Sort by monetary_value and take the top N
df_top_customers = df_intel.sort_values("current_clv", ascending=False).head(top_n)

# Create a bar chart for individual comparison
fig_user_comp = px.bar(
    df_top_customers, 
    x="user_id", 
    y="current_clv",
    color="clv_tier", # Color by tier to see how they align
    text_auto='.2s', # Adds the dollar amount on top of the bars
    title=f"Top {top_n} Customers by Lifetime Value",
    labels={"current_clv": "Total Spend ($)", "user_id": "Customer ID"},
    color_discrete_map={"High": "#00CC96", "Medium": "#636EFA", "Low": "#EF553B"}
)

# Improve readability by tilting the user IDs
fig_user_comp.update_layout(xaxis_tickangle=-45)

st.plotly_chart(fig_user_comp, use_container_width=True, key="individual_user_clv_bar")


# 1c. CLV Over Time by Segment (Line Chart)
st.subheader("CLV by Segment Over Time")

# 1. Prepare Data
df_rolling_segmented = df_rolling.merge(df_intel[['user_id', 'clv_tier']], on='user_id')

clv_trend = df_rolling_segmented.groupby(['order_date', 'clv_tier'])['running_clv'].sum().reset_index()

# 2. Create 3 Columns
col1, col2, col3 = st.columns(3)

# Define a helper function to keep the code clean
def plot_segment_trend(df, tier, color, key):
    segment_data = df[df['clv_tier'] == tier]
    fig = px.line(
        segment_data, 
        x="order_date", 
        y="running_clv",
        title=f"{tier} Segment Growth",
        labels={"running_clv": "Cumulative CLV ($)", "order_date": "Date"},
        color_discrete_sequence=[color]
    )
    # Condense the layout for side-by-side view
    fig.update_layout(height=300, margin=dict(l=20, r=20, t=40, b=20))
    st.plotly_chart(fig, use_container_width=True, key=key)

# 3. Render the 3 Charts
with col1:
    plot_segment_trend(clv_trend, "High", "#00CC96", "trend_high")

with col2:
    plot_segment_trend(clv_trend, "Medium", "#636EFA", "trend_medium")

with col3:
    plot_segment_trend(clv_trend, "Low", "#EF553B", "trend_low")

# --- 4. The Narrative Caption ---
#  or st.info() for a more emphasized style
st.info("""
**💡 Data Insight:** The downward trend in the **High Segment** indicates that our top spenders were less active in recent months. Because this is a transactional view, missing dates signify inactivity. 

Conversely, the **Medium and Low segments** show much more consistent growth and frequent "pulses," suggesting habitual spending. This highlights a need for **retention strategies** specifically targeted at re-engaging our high-value 'Whales' during their off-seasons.
""")




# --- 2. Customer Segmentation & Behavior ---
st.markdown("---")
st.header("2. Customer Segmentation & Behavior")

with st.expander("🔍 View Segmentation Definitions & Logic"):
    st.markdown("""
    | Metric | Definition | Calculation Logic |
    | :--- | :--- | :--- |
    | **Recency** | Customer Freshness | Days between last purchase and Feb 21, 2024 |
    | **Frequency** | Engagement Level | Count of orders in the last 6 months (from Feb 21, 2024) |
    | **Monetary** | Economic Value | Total spend in the last 6 months (from Feb 21, 2024) |
    
    *Segments are assigned by scoring each metric from 1-5 and clustering customers based on their combined RFM profile.*
    """)


# --- 2a. Segmentation Summary Metrics ---
st.subheader("Segment Overview")

# Calculate totals and percentages for context
total_customers = len(df_intel)
vips = len(df_intel[df_intel['segment'] == 'VIP'])
new_custs = len(df_intel[df_intel['segment'] == 'New Customer'])
churn_risk = len(df_intel[df_intel['segment'] == 'Churn Risk'])
standard = len(df_intel[df_intel['segment'] == 'Standard'])

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        label="Total VIPs", 
        value=vips, 
        help=" High Recency, Frequency, and Monetary scores (>=4)"
    )

with col2:
    st.metric(
        label=" New Customers", 
        value=new_custs, 
        help="Fresh talent: High Recency (>=4) but Low Frequency (<=2)"
    )

with col3:
    st.metric(
        label=" Churn Risk", 
        value=churn_risk, 
        delta_color="inverse",
        help="At-risk: Low Recency (<=2) and Low Frequency (<=2)."
    )

with col4:
    st.metric(
        label=" Standard", 
        value=standard, 
        help="Mid-range RFM scores (=3)"
    )



# --- 2b. Visualizing the Clusters ---
c1, c2 = st.columns([1.2, 1]) # Adjust ratio to give the chart more breathing room

with c1:
    # Subheader with a cleaner look
    st.write("### Customer Segment Distribution")
    
    fig_seg_pie = px.pie(
        df_intel, 
        names='segment', 
        hole=0.6, # Slightly larger hole for a more modern look
        color='segment',
        color_discrete_map={
            "VIP": "#00CC96", 
            "Standard": "#636EFA", 
            "New Customer": "#AB63FA", 
            "Churn Risk": "#EF553B"
        }
    )
    
    # Clean up the chart appearance
    fig_seg_pie.update_traces(textinfo='percent+label', pull=[0.05, 0, 0, 0]) # Pull the VIP slice slightly
    fig_seg_pie.update_layout(
        showlegend=False, # Hide legend if labels are on the chart for a cleaner look
        margin=dict(t=0, b=0, l=0, r=0),
        height=350
    )
    st.plotly_chart(fig_seg_pie, use_container_width=True, key="segment_pie_chart")

    
    churn_risk_pct = (churn_risk / total_customers) * 100
    st.info(f"**💡 Data Insight:** {churn_risk_pct:.1f}% of your base is currently at risk. A re-engagement email targeting the 'Churn Risk' segment could reactivate these customers.")





st.markdown("---")
st.header("3. Churn Indicators")


# --- 1d. Active Engagement of High-Value Customers ---
st.subheader("Recent Activity for High-Tier CLV Customers")

# 1. Filter data for only High CLV Tier customers
df_high_clv = df_intel[df_intel['clv_tier'] == 'High']

# 2. Calculate Counts for Activity Split
at_risk_count = len(df_high_clv[df_high_clv['frequency'] == 0])
active_count = len(df_high_clv[df_high_clv['frequency'] > 0])
total_high = len(df_high_clv)

# 3. Create Two Columns for the Visuals
col_hist, col_pie = st.columns([2, 1])

with col_hist:
    # Distribution of order frequency
    fig_high_freq = px.histogram(
        df_high_clv, 
        x="frequency", 
        nbins=20,
        title="Purchase Frequency Distribution (Last 6 Months)",
        labels={"frequency": "Number of Purchases", "count": "Customer Count"},
        color_discrete_sequence=['#00CC96'],
        text_auto=True
    )
    fig_high_freq.update_layout(bargap=0.1)
    st.plotly_chart(fig_high_freq, use_container_width=True)

with col_pie:
    # Activity vs Inactivity Pie Chart
    activity_data = pd.DataFrame({
        "Status": ["Active (Last 6m)", "Inactive (Last 6m)"],
        "Count": [active_count, at_risk_count]
    })
    
    fig_activity_pie = px.pie(
        activity_data,
        values="Count",
        names="Status",
        hole=0.5,
        title="Current Activity Status",
        color="Status",
        color_discrete_map={
            "Active (Last 6m)": "#00CC96",   # Green
            "Inactive (Last 6m)": "#EF553B"  # Red
        }
    )
    fig_activity_pie.update_layout(showlegend=False)
    fig_activity_pie.update_traces(textinfo='percent+label')
    st.plotly_chart(fig_activity_pie, use_container_width=True)

# 4. Data Insight with Dynamic Count
st.warning(f"""
**💡 Data Insight:** There are **{at_risk_count}** High-Tier CLV customers who did not make any orders in the last 6 months. 
These customers are historically high spenders and are the primary targets for win-back campaigns to prevent permanent churn.
""")

# --- 2e. "The Gap" Deep Dive ---
st.caption("These are customers in the High CLV Tier who are currently marked as Churn Risk, for marketing purposes.")

# Filter for the specific overlap
df_gap = df_intel[
    (df_intel['clv_tier'] == 'High') & 
    (df_intel['segment'] == 'Churn Risk')
]

if not df_gap.empty:
    # Format the dollar columns for the table
    df_display = df_gap[['user_id', 'current_clv', 'recency', 'frequency']].copy()
    df_display['current_clv'] = df_display['current_clv'].map("${:,.2f}".format)
    
    st.dataframe(
        df_display.sort_values('recency', ascending=False), 
        use_container_width=True,
        hide_index=True
    )





# may need more churn indicators to explore:





st.markdown("---")
st.header("4. Sales Trends Monitoring")


# Calculate the count of unique locations dynamically
unique_locations = df_trends['restaurant_id'].nunique()

st.markdown(f"""
    There are **{unique_locations}** unique business locations in this dataset. 
    Utilize the filters to analyze individual performance trends, identify peak sales periods, 
    item category performance, 
    and compare the revenue trajectory of different storefronts side-by-side.
""")

# 1. Prepare Date Columns
df_trends['order_date'] = pd.to_datetime(df_trends['order_date'])
df_trends['daily_revenue'] = pd.to_numeric(df_trends['daily_revenue'], errors='coerce')

# 2. Controls: Time Grain & Filters
col_a, col_b, col_c = st.columns([1, 1, 2])

with col_a:
    time_grain = st.radio("Select Time Grain", ["Daily", "Weekly", "Monthly"])

with col_b:
    # Filter by Location
    selected_locations = st.multiselect(
        "Filter by Restaurant", 
        options=df_trends['restaurant_id'].unique(),
        default=df_trends['restaurant_id'].unique()[:3] # Default to first 3
    )

# 3. Reshape Data based on Time Grain
df_filtered = df_trends[df_trends['restaurant_id'].isin(selected_locations)]

if time_grain == "Weekly":
    df_plot = df_filtered.groupby([pd.Grouper(key='order_date', freq='W'), 'restaurant_id'])['daily_revenue'].sum().reset_index()
    x_axis = "order_date"
elif time_grain == "Monthly":
    df_plot = df_filtered.groupby([pd.Grouper(key='order_date', freq='M'), 'restaurant_id'])['daily_revenue'].sum().reset_index()
    x_axis = "order_date"
else:
    df_plot = df_filtered.groupby(['order_date', 'restaurant_id'])['daily_revenue'].sum().reset_index()
    x_axis = "order_date"

# 4. Plot: Revenue over Time by Location
st.subheader(f"{time_grain} Revenue by Restaurant")
fig_sales = px.line(
    df_plot,
    x=x_axis,
    y="daily_revenue",
    color="restaurant_id",
    title=f"Total Sales Trend ({time_grain})",
    labels={"daily_revenue": "Revenue ($)", "order_date": "Date"},
    render_mode="svg" # Cleaner lines for time series
)
st.plotly_chart(fig_sales, use_container_width=True)

# 5. Plot: Category Mix (The "Resource Planning" Chart)
st.subheader("Item Category Performance")
# This helps identify if a location is 'Breakfast heavy' vs 'Salad heavy'
fig_cat = px.bar(
    df_filtered.groupby('item_category')['daily_revenue'].sum().sort_values(ascending=False).reset_index(),
    x="item_category",
    y="daily_revenue",
    color="item_category",
    title="Revenue Distribution by Category",
    text_auto='.2s'
)
st.plotly_chart(fig_cat, use_container_width=True)





st.markdown("---")
st.header("5. Top-Performing Locations")

# 1. Aggregate Data by Restaurant
# We take the daily trends and roll them up to a per-restaurant summary
df_store_perf = df_trends.groupby('restaurant_id').agg({
    'daily_revenue': ['sum', 'mean'],
    'daily_order_count': 'mean',
    'order_date': 'nunique' # To find the number of days the store was active
}).reset_index()

# Flatten the multi-index columns
df_store_perf.columns = [
    'restaurant_id', 'total_revenue', 'avg_daily_revenue', 
    'avg_daily_orders', 'days_active'
]

# Calculate additional metrics
df_store_perf['avg_order_value'] = df_store_perf['total_revenue'] / (df_store_perf['avg_daily_orders'] * df_store_perf['days_active'])

# Rank locations by total revenue
df_store_perf = df_store_perf.sort_values('total_revenue', ascending=False)

# 2. Metric Highlight: Best vs. Worst
best_store = df_store_perf.iloc[0]
worst_store = df_store_perf.iloc[-1]

col_best, col_worst = st.columns(2)
with col_best:
    st.success(f" **Top Performer:** {best_store['restaurant_id']}")
    st.metric("Total Revenue", f"${best_store['total_revenue']:,.0f}")
with col_worst:
    st.error(f"**Lowest Performer:** {worst_store['restaurant_id']}")
    st.metric("Total Revenue", f"${worst_store['total_revenue']:,.0f}")

# 3. Plot: Revenue Leaderboard
st.subheader("Store Revenue Ranking")
fig_rank = px.bar(
    df_store_perf,
    x='restaurant_id',
    y='total_revenue',
    color='total_revenue',
    color_continuous_scale='Viridis',
    title="Total Revenue by Location",
    labels={'total_revenue': 'Total Revenue ($)', 'restaurant_id': 'Store ID'},
    text_auto='.3s'
)
st.plotly_chart(fig_rank, use_container_width=True)

# 1. Pull dynamic values for the insight
top_location = df_store_perf.iloc[0]
second_location = df_store_perf.iloc[1]
fleet_avg = df_store_perf['total_revenue'].mean()

# 2. Calculate the "Gap" metrics
gap_to_second = top_location['total_revenue'] - second_location['total_revenue']
pct_above_avg = ((top_location['total_revenue'] - fleet_avg) / fleet_avg) * 100

# 3. Display the Insight
# 3. Enhanced Formatting for readability
st.info(f"""
**💡 Data Insight:** The top performer, 
    Store {top_location['restaurant_id']}, 
    generated {top_location['total_revenue']:,.2f}
      in total revenue—far exceeding the next top performer (Store {second_location['restaurant_id']}) by {gap_to_second:,.2f}.

Furthermore, this location outperformed the fleet average by **{pct_above_avg:,.1f}%**, cementing its status as the primary revenue driver for GlobalPartners.
""")



# 4. Plot: Efficiency Scatter (AOV vs Orders)
# Helps identify if a store is successful because of high prices or high foot traffic
st.subheader("Location Efficiency Profile")
fig_eff = px.scatter(
    df_store_perf,
    x="avg_daily_orders",
    y="avg_order_value",
    size="total_revenue",
    color="restaurant_id",
    hover_name="restaurant_id",
    title="Average Order Value vs. Daily Order Volume",
    labels={
        "avg_daily_orders": "Avg Orders per Day",
        "avg_order_value": "Avg Order Value ($)"
    }
)
st.plotly_chart(fig_eff, use_container_width=True)

# 1. Identify the 'Whale' (Highest AOV) and the 'Volume Leader' (Highest Orders)
whale_store = df_store_perf.loc[df_store_perf['avg_order_value'].idxmax()]
volume_leader = df_store_perf.loc[df_store_perf['avg_daily_orders'].idxmax()]
avg_fleet_aov = df_store_perf['avg_order_value'].mean()

# 2. Display the Dynamic Insight
st.info(f"""
**🔍 Data Insights**

* **Whale:** Store **{whale_store['restaurant_id']}** is a major outlier with an Average Order Value (AOV) of **${whale_store['avg_order_value']:,.2f}**. Despite a low frequency of **{whale_store['avg_daily_orders']:.1f} orders/day**, its high-ticket nature makes it the primary revenue driver.
* **Volume Leader:** Store **{volume_leader['restaurant_id']}** leads the fleet in foot traffic, averaging **{volume_leader['avg_daily_orders']:.1f} orders per day**. This location relies on transactional speed rather than high basket size, as its AOV is significantly lower than the catering hub.
* **The Fleet Gap:** The average AOV across all locations is **${avg_fleet_aov:,.2f}**. Locations falling significantly below this line are likely standard retail units that would benefit from "upsell" training or bundle promotions to increase ticket size.
""")




st.markdown("---")
st.header("6. Loyalty Program Impact")

# 1. Data Prep with proper Boolean handling
# Athena booleans can come in as True/False, 'true'/'false', or 1/0
df_loyalty['is_loyalty'] = df_loyalty['is_loyalty'].astype(str).str.lower()
df_loyalty['Status'] = df_loyalty['is_loyalty'].map({'true': 'Loyalty Member', '1': 'Loyalty Member', 'false': 'Non-Member', '0': 'Non-Member'})

# 2. Executive Metrics
member_stats = df_loyalty[df_loyalty['Status'] == 'Loyalty Member'].iloc[0]
non_member_stats = df_loyalty[df_loyalty['Status'] == 'Non-Member'].iloc[0]

# Calculate Metrics
aov_diff = ((member_stats['avg_order_value'] - non_member_stats['avg_order_value']) / non_member_stats['avg_order_value']) * 100
repeat_diff = ((member_stats['repeat_order_rate'] - non_member_stats['repeat_order_rate']) / non_member_stats['repeat_order_rate']) * 100

col1, col2, col3 = st.columns(3)
with col1:
    # We use a neutral delta color because 'lower' isn't necessarily 'bad' here
    st.metric(
        label="Member AOV", 
        value=f"${member_stats['avg_order_value']:,.2f}", 
        delta=f"{aov_diff:.1f}% vs Non-Member",
        delta_color="normal" 
    )
with col2:
    st.metric(
        label="Repeat Purchase Rate", 
        value=f"{member_stats['repeat_order_rate']:.2f}x", 
        delta=f"{repeat_diff:.1f}% vs Non-Member"
    )
with col3:
    loyalty_share = (member_stats['total_lifetime_value'] / df_loyalty['total_lifetime_value'].sum()) * 100
    st.metric("Loyalty Revenue Share", f"{loyalty_share:.1f}%")

# 3. The "Volume vs Value" Story
st.subheader("The Loyalty Paradox: Volume vs. Transaction Size")
c1, c2 = st.columns(2)

with c1:
    fig_aov = px.bar(
        df_loyalty, x='Status', y='avg_order_value',
        color='Status', text_auto='.2f',
        title="Average Order Value (Transaction Size)",
        color_discrete_map={'Loyalty Member': '#00CC96', 'Non-Member': '#636EFA'}
    )
    st.plotly_chart(fig_aov, use_container_width=True)

with c2:
    fig_repeat = px.bar(
        df_loyalty, x='Status', y='repeat_order_rate',
        color='Status', text_auto='.2f',
        title="Repeat Order Rate (Frequency)",
        color_discrete_map={'Loyalty Member': '#00CC96', 'Non-Member': '#636EFA'}
    )
    st.plotly_chart(fig_repeat, use_container_width=True)

# 4. Narrative Insight
st.info(f"""
**💡 Data Insight:**
Non-members have a significantly higher AOV (**${non_member_stats['avg_order_value']:,.2f}**), likely driven by large, one-off catering or group orders. 

In contrast, Loyalty Members spend less per visit (**${member_stats['avg_order_value']:,.2f}**) but have a **{repeat_diff:.1f}% higher repeat rate**. This suggests the loyalty program is successfully building **habitual behavior** (frequent small purchases) rather than driving high-ticket "Whale" transactions.
""")


st.subheader("Customer Lifetime Value (LTV) by Loyalty Status")

# 1. Calculate Per-Customer LTV (Total LTV / Total Customers)
df_loyalty['avg_ltv_per_customer'] = df_loyalty['total_lifetime_value'] / df_loyalty['total_customers']

# 2. Create Side-by-Side Columns
col_ltv_per, col_ltv_total = st.columns(2)

with col_ltv_per:
    # Bar Chart: Average LTV per Individual
    fig_ltv_per = px.bar(
        df_loyalty,
        x='Status',
        y='avg_ltv_per_customer',
        color='Status',
        text_auto='.2f',
        title="Average LTV per Individual Customer",
        labels={'avg_ltv_per_customer': 'Value per Customer ($)'},
        color_discrete_map={'Loyalty Member': '#00CC96', 'Non-Member': '#636EFA'}
    )
    fig_ltv_per.update_layout(showlegend=False)
    st.plotly_chart(fig_ltv_per, use_container_width=True)

with col_ltv_total:
    # Pie Chart: Total Revenue Share
    fig_total_share = px.pie(
        df_loyalty,
        values='total_lifetime_value',
        names='Status',
        hole=0.4,
        title="Cumulative Revenue Contribution",
        color_discrete_map={'Loyalty Member': '#00CC96', 'Non-Member': '#636EFA'}
    )
    fig_total_share.update_traces(textinfo='percent+label')
    st.plotly_chart(fig_total_share, use_container_width=True)

# 3. Dynamic Insight based on the side-by-side view
member_ltv = df_loyalty[df_loyalty['Status'] == 'Loyalty Member']['avg_ltv_per_customer'].iloc[0]
non_member_ltv = df_loyalty[df_loyalty['Status'] == 'Non-Member']['avg_ltv_per_customer'].iloc[0]

st.info(f"""
**💡 Data Insight:** Non-Members currently dominate the **Total Revenue Share** (left); an individual Non-Member is worth {non_member_ltv:,.2f} compared to {member_ltv:,.2f} for a Loyalty Member.

Highest-spending "Whales" are currently operating outside the loyalty program. Recruiting these high-value users could dramatically shift the program's ROI.
""")

st.markdown("---")
st.header("7. Pricing & Discount Effectiveness")

# Define Data
discount_data = {
    'Status': ['Discounted', 'Full Price'],
    'Order Count': [9150, 122178],
    'Total Revenue': [4695684.17, 13343599.60],
    'AOV': [113.87, 52.83]
}
df_disc = pd.DataFrame(discount_data)

# Calculate Lift
aov_lift = ((113.87 - 52.83) / 52.83) * 100

col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("AOV Comparison")
    fig_aov = px.bar(
        df_disc, x='Status', y='AOV',
        color='Status', text_auto='.2f',
        title="Average Order Value ($)",
        color_discrete_map={'Discounted': '#AB63FA', 'Full Price': '#636EFA'}
    )
    st.plotly_chart(fig_aov, use_container_width=True)

with col2:
    # 2. Revenue Efficiency (Revenue per Order Unit)
    st.subheader("Revenue Contribution")
    fig_rev_pie = px.pie(
        df_disc, values='Total Revenue', names='Status',
        hole=0.5, title="Share of Total Revenue",
        color_discrete_map={'Discounted': '#AB63FA', 'Full Price': '#636EFA'}
    )
    st.plotly_chart(fig_rev_pie, use_container_width=True)

    # Calculate % of total orders vs % of total revenue
total_orders = df_disc['Order Count'].sum()
total_rev = df_disc['Total Revenue'].sum()
disc_order_pct = (9150 / total_orders) * 100
disc_rev_pct = (4695684 / total_rev) * 100

st.write("### Discount ROI")
c1, c2, c3 = st.columns(3)

with c1:
    st.metric("AOV Lift from Discounts", f"{aov_lift:.1f}%", help="Discounted orders spend significantly more per transaction.")
with c2:
    st.metric("Order Share", f"{disc_order_pct:.1f}%", help="Percentage of total orders that used a discount.")
with c3:
    st.metric("Revenue Contribution", f"{disc_rev_pct:.1f}%", help="Percentage of total revenue driven by discounted orders.")

# 5. Fixed Dynamic Data Insight
st.info(f"""
**💡 Data Insight:** While discounted orders represent only **{disc_order_pct:.1f}%** of total volume, they have an Average Order Value of **${df_disc.loc[0, 'AOV']:,.2f}**. 

This is **{abs(aov_lift):.1f}% {"higher" if aov_lift > 0 else "lower"}** than full-price orders, confirming that discounts are successfully driving "upselling" behavior—likely pushing customers to add more items to their cart to meet discount thresholds.
""")









