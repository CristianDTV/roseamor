-- 1. Executive KPIs (single-row scorecard)
SELECT
    ROUND(SUM(revenue),       2)                          AS total_revenue,
    ROUND(SUM(gross_margin),  2)                          AS total_margin,
    COUNT(DISTINCT order_id)                              AS total_orders,
    ROUND(SUM(revenue) / COUNT(DISTINCT order_id), 2)    AS avg_ticket,
    ROUND(SUM(gross_margin) / SUM(revenue) * 100, 1)     AS margin_pct,
    SUM(quantity)                                         AS total_units_sold
FROM fact_sales;

-- 2. Monthly Revenue Trend (with MoM growth %)
WITH monthly AS (
    SELECT
        d.year,
        d.month,
        d.month_name,
        ROUND(SUM(f.revenue),      2) AS revenue,
        ROUND(SUM(f.gross_margin), 2) AS margin,
        COUNT(DISTINCT f.order_id)   AS orders
    FROM fact_sales f
    JOIN dim_date   d ON f.date_id = d.date_id
    GROUP BY d.year, d.month, d.month_name
)
SELECT
    year,
    month,
    month_name,
    revenue,
    margin,
    orders,
    ROUND(
        (revenue - LAG(revenue) OVER (ORDER BY year, month))
        / LAG(revenue) OVER (ORDER BY year, month) * 100,
    1) AS mom_revenue_growth_pct
FROM monthly
ORDER BY year, month;

-- 3. Revenue & Margin by Channel
SELECT
    channel,
    COUNT(DISTINCT order_id)                                AS orders,
    SUM(quantity)                                           AS units,
    ROUND(SUM(revenue),      2)                             AS revenue,
    ROUND(SUM(gross_margin), 2)                             AS margin,
    ROUND(SUM(gross_margin) / SUM(revenue) * 100, 1)       AS margin_pct,
    ROUND(SUM(revenue) / COUNT(DISTINCT order_id), 2)      AS avg_ticket,
    ROUND(SUM(revenue) / SUM(SUM(revenue)) OVER () * 100, 1) AS revenue_share_pct
FROM fact_sales
GROUP BY channel
ORDER BY revenue DESC;

-- 4. Margin by Product Category
SELECT
    p.category,
    COUNT(DISTINCT f.order_id)                               AS orders,
    ROUND(SUM(f.revenue),       2)                           AS revenue,
    ROUND(SUM(f.gross_margin),  2)                           AS margin,
    ROUND(SUM(f.gross_margin) / SUM(f.revenue) * 100, 1)    AS margin_pct,
    ROUND(AVG(f.unit_price),    2)                           AS avg_unit_price
FROM fact_sales f
JOIN dim_product p ON f.sku = p.sku
GROUP BY p.category
ORDER BY margin DESC;

-- 5. Top 10 Customers by Revenue
WITH customer_revenue AS (
    SELECT
        f.customer_id,
        c.name,
        c.country,
        c.segment,
        COUNT(DISTINCT f.order_id)                               AS orders,
        ROUND(SUM(f.revenue),       2)                           AS revenue,
        ROUND(SUM(f.gross_margin),  2)                           AS margin,
        ROUND(SUM(f.revenue) / COUNT(DISTINCT f.order_id), 2)   AS avg_ticket,
        ROUND(SUM(f.revenue) / SUM(SUM(f.revenue)) OVER () * 100, 2) AS revenue_share_pct,
        RANK() OVER (ORDER BY SUM(f.revenue) DESC)               AS revenue_rank
    FROM fact_sales f
    JOIN dim_customer c ON f.customer_id = c.customer_id
    GROUP BY f.customer_id, c.name, c.country, c.segment
)
SELECT *
FROM customer_revenue
WHERE revenue_rank <= 10
ORDER BY revenue_rank;

-- 6. Top 10 Products by Revenue
WITH product_revenue AS (
    SELECT
        f.sku,
        p.category,
        COUNT(DISTINCT f.order_id)                               AS orders,
        SUM(f.quantity)                                          AS units_sold,
        ROUND(SUM(f.revenue),       2)                           AS revenue,
        ROUND(SUM(f.gross_margin),  2)                           AS margin,
        ROUND(SUM(f.gross_margin) / SUM(f.revenue) * 100, 1)    AS margin_pct,
        RANK() OVER (ORDER BY SUM(f.revenue) DESC)               AS revenue_rank
    FROM fact_sales f
    JOIN dim_product p ON f.sku = p.sku
    GROUP BY f.sku, p.category
)
SELECT *
FROM product_revenue
WHERE revenue_rank <= 10
ORDER BY revenue_rank;

-- 7. Revenue by Country
SELECT
    c.country,
    COUNT(DISTINCT f.order_id)                               AS orders,
    COUNT(DISTINCT f.customer_id)                            AS customers,
    ROUND(SUM(f.revenue),       2)                           AS revenue,
    ROUND(SUM(f.gross_margin),  2)                           AS margin,
    ROUND(SUM(f.revenue) / COUNT(DISTINCT f.order_id), 2)   AS avg_ticket
FROM fact_sales f
JOIN dim_customer c ON f.customer_id = c.customer_id
GROUP BY c.country
ORDER BY revenue DESC;

-- 8. Customer Segmentation Performance
SELECT
    c.segment,
    COUNT(DISTINCT f.customer_id)                             AS customers,
    COUNT(DISTINCT f.order_id)                                AS orders,
    ROUND(SUM(f.revenue),       2)                            AS revenue,
    ROUND(SUM(f.gross_margin),  2)                            AS margin,
    ROUND(SUM(f.revenue) / COUNT(DISTINCT f.order_id), 2)    AS avg_ticket,
    ROUND(SUM(f.revenue) / COUNT(DISTINCT f.customer_id), 2) AS revenue_per_customer
FROM fact_sales f
JOIN dim_customer c ON f.customer_id = c.customer_id
GROUP BY c.segment
ORDER BY revenue DESC;

-- 9. Quarterly Performance Summary
SELECT
    d.year,
    d.quarter,
    ROUND(SUM(f.revenue),      2)                               AS revenue,
    ROUND(SUM(f.gross_margin), 2)                               AS margin,
    COUNT(DISTINCT f.order_id)                                AS orders,
    ROUND(SUM(f.revenue) / COUNT(DISTINCT f.order_id), 2)    AS avg_ticket,
    ROUND(
        (SUM(f.revenue) - LAG(SUM(f.revenue)) OVER (ORDER BY d.year, d.quarter))
        / LAG(SUM(f.revenue)) OVER (ORDER BY d.year, d.quarter) * 100,
    1) AS qoq_growth_pct
FROM fact_sales f
JOIN dim_date   d ON f.date_id = d.date_id
GROUP BY d.year, d.quarter
ORDER BY d.year, d.quarter;

-- 10. Active vs Inactive Product Revenue Contribution
SELECT
    CASE WHEN p.active = 1 THEN 'Active' ELSE 'Inactive' END AS product_status,
    COUNT(DISTINCT f.sku)                                      AS skus,
    COUNT(DISTINCT f.order_id)                                 AS orders,
    ROUND(SUM(f.revenue),      2)                              AS revenue,
    ROUND(SUM(f.gross_margin), 2)                              AS margin
FROM fact_sales f
JOIN dim_product p ON f.sku = p.sku
GROUP BY p.active;

-- 11. Data Quality Audit (Returns and excluded rows)
SELECT
    'Returns (negative qty)'   AS issue_type,
    COUNT(*)                   AS row_count,
    ROUND(SUM(ABS(quantity) * unit_price), 2) AS implied_revenue_impact
FROM stg_orders
WHERE is_return = 1;