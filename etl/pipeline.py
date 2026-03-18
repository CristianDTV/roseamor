import pandas as pd
import sqlite3
import logging
import hashlib
import sys
from pathlib import Path
from datetime import datetime

# Config

BASE_DIR   = Path(__file__).resolve().parent.parent
RAW_DIR    = BASE_DIR / "data" / "raw"
DB_PATH    = BASE_DIR / "data" / "roseamor.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("roseamor.pipeline")

# Extract

def load_raw(conn: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    log.info("── LAYER 1: Loading raw CSVs ──")

    tables = {
        "customers": RAW_DIR / "customers.csv",
        "orders":    RAW_DIR / "orders.csv",
        "products":  RAW_DIR / "products.csv",
    }
    raw: dict[str, pd.DataFrame] = {}

    for name, path in tables.items():
        df = pd.read_csv(path, dtype=str)
        df["_source_file"]  = path.name
        df["_loaded_at"]    = datetime.utcnow().isoformat()
        df["_row_hash"] = df.apply(
            lambda r: hashlib.md5("|".join([str(v) for v in r]).encode()).hexdigest(), axis=1
        )
        df.to_sql(f"raw_{name}", conn, if_exists="replace", index=False)
        raw[name] = df
        log.info(f"  raw_{name}: {len(df):,} rows loaded from {path.name}")

    return raw

def _qa_report(df: pd.DataFrame, label: str) -> dict:
    return {
        "table":      label,
        "total_rows": len(df),
        "issues":     {},
    }

# Transform

def stage_customers(raw_df: pd.DataFrame, conn: sqlite3.Connection) -> pd.DataFrame:
    log.info("── LAYER 2: Staging customers ──")
    df = raw_df.copy()
    df = df[[c for c in df.columns if not c.startswith("_")]]
    issues: dict = {}

    dupes = df.duplicated(subset="customer_id", keep="first")
    issues["duplicate_customer_id"] = int(dupes.sum())
    df = df[~dupes].copy()
    if issues["duplicate_customer_id"]:
        log.warning(f"  Removed {issues['duplicate_customer_id']} duplicate customer_id rows")

    null_country  = df["country"].isnull().sum()
    null_segment  = df["segment"].isnull().sum()
    issues["null_country"]  = int(null_country)
    issues["null_segment"]  = int(null_segment)
    df["country"]  = df["country"].fillna("Unknown")
    df["segment"]  = df["segment"].fillna("Unknown")
    if null_country:  log.warning(f"  {null_country} null country  → filled with 'Unknown'")
    if null_segment:  log.warning(f"  {null_segment} null segment  → filled with 'Unknown'")

    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    invalid_dates = df["created_at"].isnull().sum()
    issues["invalid_created_at"] = int(invalid_dates)
    if invalid_dates:
        log.warning(f"  {invalid_dates} invalid created_at → rows kept, date set to NaT")

    df["country"] = df["country"].str.strip().str.title()
    df["segment"] = df["segment"].str.strip().str.title()
    df["name"]    = df["name"].str.strip()

    df["_stg_loaded_at"] = datetime.utcnow().isoformat()
    df.to_sql("stg_customers", conn, if_exists="replace", index=False)
    log.info(f"  stg_customers: {len(df):,} clean rows  | issues={issues}")
    return df


def stage_products(raw_df: pd.DataFrame, conn: sqlite3.Connection) -> pd.DataFrame:
    log.info("── LAYER 2: Staging products ──")
    df = raw_df.copy()
    df = df[[c for c in df.columns if not c.startswith("_")]]
    issues: dict = {}

    dupes = df.duplicated(subset="sku", keep="first")
    issues["duplicate_sku"] = int(dupes.sum())
    df = df[~dupes].copy()

    null_cat = df["category"].isnull().sum()
    issues["null_category"] = int(null_cat)
    df["category"] = df["category"].fillna("Uncategorized")
    if null_cat: log.warning(f"  {null_cat} null category  → filled with 'Uncategorized'")

    df["cost"] = pd.to_numeric(df["cost"], errors="coerce")
    neg_cost = (df["cost"] < 0).sum()
    issues["negative_cost"] = int(neg_cost)
    df.loc[df["cost"] < 0, "cost"] = df["cost"].abs()
    if neg_cost: log.warning(f"  {neg_cost} negative cost → converted to absolute value")

    null_cost = df["cost"].isnull().sum()
    issues["null_cost"] = int(null_cost)
    if null_cost:
        median_cost = df["cost"].median()
        df["cost"] = df["cost"].fillna(median_cost)
        log.warning(f"  {null_cost} null cost → filled with median {median_cost:.2f}")

    df["active"] = df["active"].map({"True": True, "False": False, True: True, False: False})

    df["_stg_loaded_at"] = datetime.utcnow().isoformat()
    df.to_sql("stg_products", conn, if_exists="replace", index=False)
    log.info(f"  stg_products: {len(df):,} clean rows  | issues={issues}")
    return df


def stage_orders(raw_df: pd.DataFrame, conn: sqlite3.Connection) -> pd.DataFrame:
    log.info("── LAYER 2: Staging orders ──")
    df = raw_df.copy()
    df = df[[c for c in df.columns if not c.startswith("_")]]
    issues: dict = {}

    dupes = df.duplicated(subset="order_id", keep="first")
    issues["duplicate_order_id"] = int(dupes.sum())
    df = df[~dupes].copy()
    if issues["duplicate_order_id"]:
        log.warning(f"  Removed {issues['duplicate_order_id']} duplicate order_id rows")

    df["quantity"]   = pd.to_numeric(df["quantity"],   errors="coerce")
    df["unit_price"] = pd.to_numeric(df["unit_price"], errors="coerce")
    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")

    invalid_dates = df["order_date"].isnull().sum()
    issues["invalid_order_date"] = int(invalid_dates)
    df = df[df["order_date"].notnull()].copy()
    if invalid_dates:
        log.warning(f"  {invalid_dates} invalid order_date → rows excluded")

    # Flag returns instead of dropping to maintain audit integrity
    neg_qty = (df["quantity"] < 0).sum()
    issues["negative_quantity"] = int(neg_qty)
    df["is_return"] = df["quantity"] < 0
    if neg_qty: log.warning(f"  {neg_qty} negative quantity → flagged as is_return=True, kept for audit")

    null_price = df["unit_price"].isnull().sum()
    issues["null_unit_price"] = int(null_price)
    df = df[df["unit_price"].notnull()].copy()
    if null_price:
        log.warning(f"  {null_price} null unit_price → rows excluded (revenue undefined)")

    df["channel"] = df["channel"].str.strip().str.lower()
    df["revenue"]        = df["quantity"] * df["unit_price"]
    df["order_date"]     = df["order_date"].dt.normalize()

    df["_stg_loaded_at"] = datetime.utcnow().isoformat()
    df.to_sql("stg_orders", conn, if_exists="replace", index=False)
    log.info(f"  stg_orders: {len(df):,} clean rows  | issues={issues}")
    return df

# Validation

def quarantine_orders(
    stg_orders: pd.DataFrame,
    stg_customers: pd.DataFrame,
    stg_products: pd.DataFrame,
    conn: sqlite3.Connection,
) -> pd.DataFrame:
    log.info("── LAYER 2b: Semantic validation (quarantine) ──")
    df = stg_orders.copy()

    cust_dates = stg_customers[["customer_id", "created_at"]].copy()
    cust_dates["created_at"] = pd.to_datetime(cust_dates["created_at"], errors="coerce")
    df = df.merge(cust_dates, on="customer_id", how="left")
    
    df["is_pre_signup"] = df["order_date"] < df["created_at"]
    pre_signup_count = int(df["is_pre_signup"].sum())
    
    if pre_signup_count:
        log.warning(
            f"  {pre_signup_count} orders where order_date < customer created_at "
            f"→ flagged is_pre_signup=True, isolated in orders_quarantine"
        )
        
    quarantine = df[df["is_pre_signup"] == True].copy()
    quarantine["_quarantine_reason"] = "order_date < customer created_at"
    quarantine["_quarantined_at"]    = datetime.utcnow().isoformat()
    quarantine.to_sql("orders_quarantine", conn, if_exists="replace", index=False)
    log.info(f"  orders_quarantine: {len(quarantine):,} rows")
    df = df.drop(columns=["created_at"], errors="ignore")

    prod_meta = stg_products[["sku", "active", "cost"]].copy()
    df = df.merge(prod_meta, on="sku", how="left", suffixes=("", "_prod"))
    df["is_inactive_sale"] = df["active"] == False
    inactive_count = int(df["is_inactive_sale"].sum())
    
    if inactive_count:
        log.warning(f"  {inactive_count} orders on inactive products → flagged is_inactive_sale=True")

    df["is_below_cost"] = df["unit_price"] < df["cost"]
    below_cost_count = int(df["is_below_cost"].sum())
    
    if below_cost_count:
        log.warning(
            f"  {below_cost_count} orders where unit_price < cost "
            f"→ flagged is_below_cost=True (may be promos/liquidations)"
        )

    df = df.drop(columns=["active", "cost"], errors="ignore")
    df.to_sql("stg_orders", conn, if_exists="replace", index=False)
    
    log.info(
        f"  Semantic flags — is_pre_signup={pre_signup_count}, "
        f"is_inactive_sale={inactive_count}, is_below_cost={below_cost_count}"
    )
    return df

# Load (Marts)

def build_marts(
    stg_customers: pd.DataFrame,
    stg_products:  pd.DataFrame,
    stg_orders:    pd.DataFrame,
    conn: sqlite3.Connection,
) -> None:
    log.info("── LAYER 3: Building marts (star schema) ──")

    # Dimensions
    dim_customer = stg_customers[[
        "customer_id", "name", "country", "segment", "created_at"
    ]].copy()
    dim_customer.to_sql("dim_customer", conn, if_exists="replace", index=False)
    log.info(f"  dim_customer: {len(dim_customer):,} rows")

    dim_product = stg_products[["sku", "category", "cost", "active"]].copy()
    dim_product.to_sql("dim_product", conn, if_exists="replace", index=False)
    log.info(f"  dim_product: {len(dim_product):,} rows")

    min_year = stg_orders["order_date"].min().year
    max_year = stg_orders["order_date"].max().year
    
    dates = pd.date_range(
        start=f"{min_year}-01-01",
        end=f"{max_year}-12-31",
        freq="D"
    )
    dim_date = pd.DataFrame({
        "date_id":      dates.strftime("%Y%m%d").astype(int),
        "date":         dates,
        "year":         dates.year,
        "quarter":      dates.quarter,
        "month":        dates.month,
        "month_name":   dates.strftime("%B"),
        "week":         dates.isocalendar().week.values,
        "day_of_week":  dates.day_name(),
        "is_weekend":   dates.dayofweek >= 5,
    })
    dim_date.to_sql("dim_date", conn, if_exists="replace", index=False)
    log.info(f"  dim_date: {len(dim_date):,} rows")

    # Facts
    sales = stg_orders[~stg_orders["is_return"]].copy()
    sales = sales.merge(
        stg_products[["sku", "cost"]], on="sku", how="left"
    )
    sales["cost_total"]    = sales["quantity"] * sales["cost"]
    sales["gross_margin"]  = sales["revenue"] - sales["cost_total"]
    sales["margin_pct"]    = (sales["gross_margin"] / sales["revenue"]).round(4)
    sales["date_id"]       = sales["order_date"].dt.strftime("%Y%m%d").astype(int)

    for flag in ("is_pre_signup", "is_inactive_sale", "is_below_cost"):
        if flag not in sales.columns:
            sales[flag] = False
        else:
            sales[flag] = sales[flag].fillna(False)

    fact_sales = sales[[
        "order_id", "customer_id", "sku", "channel",
        "date_id", "order_date",
        "quantity", "unit_price", "revenue",
        "cost_total", "gross_margin", "margin_pct",
        "is_pre_signup", "is_inactive_sale", "is_below_cost",
    ]]
    fact_sales.to_sql("fact_sales", conn, if_exists="replace", index=False)
    log.info(f"  fact_sales: {len(fact_sales):,} rows")

    # Aggregations
    monthly = (
        fact_sales
        .groupby(["date_id", "channel"])
        .agg(
            orders=("order_id",    "nunique"),
            revenue=("revenue",    "sum"),
            margin=("gross_margin","sum"),
            units=("quantity",     "sum"),
        )
        .reset_index()
    )
    monthly.to_sql("mart_monthly_summary", conn, if_exists="replace", index=False)
    log.info(f"  mart_monthly_summary: {len(monthly):,} rows")

    clv = (
        fact_sales
        .groupby("customer_id")
        .agg(
            total_orders=("order_id",    "nunique"),
            total_revenue=("revenue",    "sum"),
            total_margin=("gross_margin","sum"),
            avg_order_value=("revenue",  "mean"),
            first_order=("order_date",   "min"),
            last_order=("order_date",    "max"),
        )
        .reset_index()
    )
    clv = clv.merge(
        dim_customer[["customer_id","name","country","segment"]],
        on="customer_id", how="left"
    )
    clv.to_sql("mart_customer_lifetime", conn, if_exists="replace", index=False)
    log.info(f"  mart_customer_lifetime: {len(clv):,} rows")

    prod_perf = (
        fact_sales
        .groupby("sku")
        .agg(
            total_orders=("order_id",    "nunique"),
            total_units=("quantity",     "sum"),
            total_revenue=("revenue",    "sum"),
            total_margin=("gross_margin","sum"),
        )
        .reset_index()
    )
    prod_perf = prod_perf.merge(dim_product, on="sku", how="left")
    prod_perf.to_sql("mart_product_performance", conn, if_exists="replace", index=False)
    log.info(f"  mart_product_performance: {len(prod_perf):,} rows")

    log.info("── LAYER 3 complete ──")

# Main

def run_pipeline() -> None:
    log.info("═" * 60)
    log.info("RoseAmor Data Pipeline  —  started")
    log.info(f"DB: {DB_PATH}")
    log.info("═" * 60)

    conn = sqlite3.connect(DB_PATH)

    try:
        raw      = load_raw(conn)
        stg_cust = stage_customers(raw["customers"], conn)
        stg_prod = stage_products(raw["products"],   conn)
        stg_ord  = stage_orders(raw["orders"],       conn)
        stg_ord  = quarantine_orders(stg_ord, stg_cust, stg_prod, conn)
        
        build_marts(stg_cust, stg_prod, stg_ord, conn)
        conn.commit()
        
        log.info("═" * 60)
        log.info("Pipeline finished successfully ✓")
        log.info("═" * 60)
    except Exception as exc:
        conn.rollback()
        log.error(f"Pipeline FAILED: {exc}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run_pipeline()