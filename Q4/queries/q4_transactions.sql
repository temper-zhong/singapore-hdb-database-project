-- Same extraction as Q4/hdb_cpi_adjusted_price_analysis.py.
-- Main endpoint: 2025-Q4. The query also retains 2026-Q1/Q2 for supplements.
SELECT
    r.resale_id,
    r.month_id,
    r.quarter_id,
    r.year,
    r.block_id,
    t.town_name,
    f.flat_type,
    r.resale_price_nominal_sgd,
    r.resale_price_real_2024_sgd,
    r.price_per_sqm_nominal_sgd,
    r.price_per_sqm_real_2024_sgd,
    r.cpi_all_items_2024_base,
    r.potential_duplicate_flag
FROM fact_resale_transaction AS r
JOIN dim_town AS t ON t.town_id = r.town_id
JOIN dim_flat_type AS f ON f.flat_type_id = r.flat_type_id
JOIN dim_time_month AS tm ON tm.month_id = r.month_id
WHERE tm.complete_quarter_flag = TRUE
  AND r.quarter_id >= '2017-Q1'
  AND r.quarter_id <= '2026-Q2'
  AND r.resale_price_nominal_sgd > 0
  AND r.resale_price_real_2024_sgd > 0
  AND r.price_per_sqm_nominal_sgd > 0
  AND r.price_per_sqm_real_2024_sgd > 0
ORDER BY r.quarter_id, r.resale_id;
