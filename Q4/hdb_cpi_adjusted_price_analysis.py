"""RQ4: Does the HDB resale-price increase remain after CPI adjustment?

The analysis reads transaction data only through SQL.  By default it connects
to the shared MySQL database named ``hdb_project``.  A SQLite connection option
is included so the same SQL/analysis path can be tested without changing the
research code.

Default MySQL run:
    python hdb_cpi_adjusted_price_analysis.py

Environment variables accepted:
    HDB_DB_HOST, HDB_DB_PORT, HDB_DB_USER, HDB_DB_NAME, HDB_DB_PASSWORD

Install dependencies:
    python -m pip install PyMySQL pandas numpy Pillow
"""

from __future__ import annotations

import argparse
import getpass
import json
import math
import os
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont


START_QUARTER = "2017-Q1"
MAIN_END_QUARTER = "2025-Q4"
LATEST_END_QUARTER = "2026-Q2"
MIN_CELL_N = 10
HAC_LAGS = 4

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "hdb_cpi_adjusted_price_analysis_output"

COLORS = {
    "nominal": "#285F8F",
    "real": "#D97828",
    "grid": "#D9E0E6",
    "axis": "#66727E",
    "text": "#16212B",
    "muted": "#5D6873",
    "accent": "#248277",
}


SQL = f"""
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
JOIN dim_town AS t
    ON t.town_id = r.town_id
JOIN dim_flat_type AS f
    ON f.flat_type_id = r.flat_type_id
JOIN dim_time_month AS tm
    ON tm.month_id = r.month_id
WHERE tm.complete_quarter_flag = TRUE
  AND r.quarter_id >= '{START_QUARTER}'
  AND r.quarter_id <= '{LATEST_END_QUARTER}'
  AND r.resale_price_nominal_sgd > 0
  AND r.resale_price_real_2024_sgd > 0
  AND r.price_per_sqm_nominal_sgd > 0
  AND r.price_per_sqm_real_2024_sgd > 0
ORDER BY r.quarter_id, r.resale_id
"""


NUMERIC_COLUMNS = [
    "resale_price_nominal_sgd",
    "resale_price_real_2024_sgd",
    "price_per_sqm_nominal_sgd",
    "price_per_sqm_real_2024_sgd",
    "cpi_all_items_2024_base",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite",
        type=Path,
        help="Use this SQLite database instead of the default MySQL database.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for CSV, JSON and PNG outputs.",
    )
    parser.add_argument("--db-host", default=os.environ.get("HDB_DB_HOST", "localhost"))
    parser.add_argument("--db-port", type=int, default=int(os.environ.get("HDB_DB_PORT", "3306")))
    parser.add_argument("--db-user", default=os.environ.get("HDB_DB_USER", "root"))
    parser.add_argument("--db-name", default=os.environ.get("HDB_DB_NAME", "hdb_project"))
    return parser.parse_args()


def connect(args: argparse.Namespace) -> tuple[Any, str]:
    if args.sqlite is not None:
        if not args.sqlite.exists():
            raise FileNotFoundError(f"SQLite database not found: {args.sqlite}")
        return sqlite3.connect(args.sqlite), f"SQLite ({args.sqlite.name})"

    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError(
            "PyMySQL is required for the default MySQL connection. "
            "Install it with: python -m pip install PyMySQL"
        ) from exc

    password = os.environ.get("HDB_DB_PASSWORD") or getpass.getpass("MySQL password: ")
    connection = pymysql.connect(
        host=args.db_host,
        port=args.db_port,
        user=args.db_user,
        password=password,
        database=args.db_name,
        charset="utf8mb4",
    )
    return connection, f"MySQL ({args.db_name})"


def quarter_sort_key(value: str) -> tuple[int, int]:
    year_text, quarter_text = value.split("-Q")
    return int(year_text), int(quarter_text)


def quarter_distance(start: str, end: str) -> int:
    sy, sq = quarter_sort_key(start)
    ey, eq = quarter_sort_key(end)
    return (ey - sy) * 4 + (eq - sq)


def fetch_data(args: argparse.Namespace) -> tuple[pd.DataFrame, str]:
    connection, database_label = connect(args)
    try:
        data = pd.read_sql_query(SQL, connection)
    finally:
        connection.close()

    if data.empty:
        raise RuntimeError("The SQL query returned no rows. Check the database import and connection settings.")

    for column in NUMERIC_COLUMNS:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["year"] = pd.to_numeric(data["year"], errors="coerce").astype("Int64")
    data["quarter_id"] = data["quarter_id"].astype(str)
    data["month_id"] = pd.to_datetime(data["month_id"], errors="coerce")

    duplicate_raw = data["potential_duplicate_flag"]
    if duplicate_raw.dtype == bool:
        duplicate_flag = duplicate_raw
    else:
        duplicate_flag = duplicate_raw.astype(str).str.strip().str.lower().isin({"1", "true", "t", "yes"})
    data["potential_duplicate_flag"] = duplicate_flag

    required = [
        "resale_id",
        "month_id",
        "quarter_id",
        "year",
        "town_name",
        "flat_type",
        *NUMERIC_COLUMNS,
    ]
    data = data.dropna(subset=required).copy()
    data = data[
        (data["quarter_id"] >= START_QUARTER)
        & (data["quarter_id"] <= LATEST_END_QUARTER)
    ].copy()
    data["year"] = data["year"].astype(int)

    quarter_order = sorted(data["quarter_id"].unique(), key=quarter_sort_key)
    if quarter_order[0] != START_QUARTER or MAIN_END_QUARTER not in quarter_order:
        raise RuntimeError(
            f"Expected data from {START_QUARTER} through at least {MAIN_END_QUARTER}; "
            f"received {quarter_order[0]} through {quarter_order[-1]}."
        )
    return data, database_label


def quarterly_summary(data: pd.DataFrame) -> pd.DataFrame:
    summary = (
        data.groupby("quarter_id", as_index=False, observed=True)
        .agg(
            transaction_n=("resale_id", "size"),
            town_n=("town_name", "nunique"),
            flat_type_n=("flat_type", "nunique"),
            median_resale_price_nominal_sgd=("resale_price_nominal_sgd", "median"),
            median_resale_price_real_2024_sgd=("resale_price_real_2024_sgd", "median"),
            median_price_per_sqm_nominal_sgd=("price_per_sqm_nominal_sgd", "median"),
            median_price_per_sqm_real_2024_sgd=("price_per_sqm_real_2024_sgd", "median"),
        )
        .sort_values("quarter_id", key=lambda values: values.map(quarter_sort_key))
        .reset_index(drop=True)
    )

    monthly_cpi = data[["month_id", "quarter_id", "cpi_all_items_2024_base"]].drop_duplicates("month_id")
    q_cpi = (
        monthly_cpi.groupby("quarter_id", as_index=False)
        .agg(quarter_cpi_all_items_2024_base=("cpi_all_items_2024_base", "mean"))
    )
    summary = summary.merge(q_cpi, on="quarter_id", how="left", validate="one_to_one")

    metric_columns = [
        "median_resale_price_nominal_sgd",
        "median_resale_price_real_2024_sgd",
        "median_price_per_sqm_nominal_sgd",
        "median_price_per_sqm_real_2024_sgd",
    ]
    for column in metric_columns:
        baseline = float(summary.loc[summary["quarter_id"] == START_QUARTER, column].iloc[0])
        summary[f"{column}_index"] = summary[column] / baseline * 100.0
        summary[f"{column}_yoy_pct"] = summary[column].pct_change(4, fill_method=None) * 100.0
    return summary


def annual_summary(data: pd.DataFrame) -> pd.DataFrame:
    main = data[data["quarter_id"] <= MAIN_END_QUARTER].copy()
    summary = (
        main.groupby("year", as_index=False)
        .agg(
            transaction_n=("resale_id", "size"),
            median_resale_price_nominal_sgd=("resale_price_nominal_sgd", "median"),
            median_resale_price_real_2024_sgd=("resale_price_real_2024_sgd", "median"),
            median_price_per_sqm_nominal_sgd=("price_per_sqm_nominal_sgd", "median"),
            median_price_per_sqm_real_2024_sgd=("price_per_sqm_real_2024_sgd", "median"),
        )
        .sort_values("year")
        .reset_index(drop=True)
    )
    monthly_cpi = main[["month_id", "cpi_all_items_2024_base"]].drop_duplicates("month_id").copy()
    monthly_cpi["year"] = monthly_cpi["month_id"].dt.year
    y_cpi = monthly_cpi.groupby("year", as_index=False).agg(
        annual_cpi_all_items_2024_base=("cpi_all_items_2024_base", "mean")
    )
    summary = summary.merge(y_cpi, on="year", how="left", validate="one_to_one")
    for column in [
        "median_resale_price_nominal_sgd",
        "median_resale_price_real_2024_sgd",
        "median_price_per_sqm_nominal_sgd",
        "median_price_per_sqm_real_2024_sgd",
        "annual_cpi_all_items_2024_base",
    ]:
        summary[f"{column}_vs_2017_pct"] = (summary[column] / summary[column].iloc[0] - 1.0) * 100.0
    return summary


def composition_adjusted_index(data: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Create a fixed-weight Town x flat-type price-per-sqm index.

    Stable strata must have at least MIN_CELL_N transactions in every quarter
    from 2017-Q1 through 2025-Q4.  Pooled 2017-2025 transaction shares are then
    held fixed, and quarterly stratum medians are aggregated geometrically.
    """

    main = data[data["quarter_id"] <= MAIN_END_QUARTER].copy()
    main_quarters = sorted(main["quarter_id"].unique(), key=quarter_sort_key)
    expected_quarters = quarter_distance(START_QUARTER, MAIN_END_QUARTER) + 1
    if len(main_quarters) != expected_quarters:
        raise RuntimeError(
            f"The main period should contain {expected_quarters} quarters; found {len(main_quarters)}."
        )

    cell_summary = (
        data.groupby(["quarter_id", "town_name", "flat_type"], as_index=False, observed=True)
        .agg(
            cell_n=("resale_id", "size"),
            median_ppsm_nominal=("price_per_sqm_nominal_sgd", "median"),
            median_ppsm_real=("price_per_sqm_real_2024_sgd", "median"),
        )
    )
    main_cells = cell_summary[cell_summary["quarter_id"] <= MAIN_END_QUARTER]
    coverage = main_cells.groupby(["town_name", "flat_type"], observed=True).agg(
        quarters_present=("quarter_id", "nunique"),
        minimum_quarter_n=("cell_n", "min"),
    )
    stable_index = coverage[
        (coverage["quarters_present"] == expected_quarters)
        & (coverage["minimum_quarter_n"] >= MIN_CELL_N)
    ].index
    if len(stable_index) == 0:
        raise RuntimeError("No stable Town x flat-type strata meet the sample rule.")

    stable_keys = pd.DataFrame(stable_index.tolist(), columns=["town_name", "flat_type"])
    stable_main = main.merge(stable_keys, on=["town_name", "flat_type"], how="inner")
    weights = (
        stable_main.groupby(["town_name", "flat_type"], observed=True)
        .size()
        .rename("pooled_n")
        .to_frame()
    )
    weights["weight"] = weights["pooled_n"] / weights["pooled_n"].sum()

    cells = cell_summary.set_index(["town_name", "flat_type"])
    rows: list[dict[str, Any]] = []
    for quarter in sorted(data["quarter_id"].unique(), key=quarter_sort_key):
        qcells = cells[cells["quarter_id"] == quarter]
        present = stable_index.intersection(qcells.index)
        if len(present) != len(stable_index):
            missing = len(stable_index) - len(present)
            raise RuntimeError(f"{quarter} is missing {missing} stable strata.")
        qw = weights.loc[present, "weight"]
        qw = qw / qw.sum()
        qdata = qcells.loc[present]
        nominal_level = float(np.exp(np.sum(qw * np.log(qdata["median_ppsm_nominal"]))))
        real_level = float(np.exp(np.sum(qw * np.log(qdata["median_ppsm_real"]))))
        rows.append(
            {
                "quarter_id": quarter,
                "stable_strata_n": len(stable_index),
                "stable_strata_transaction_n": int(qdata["cell_n"].sum()),
                "fixed_weight_geomean_ppsm_nominal_sgd": nominal_level,
                "fixed_weight_geomean_ppsm_real_2024_sgd": real_level,
            }
        )

    result = pd.DataFrame(rows)
    nominal_base = float(result.loc[result["quarter_id"] == START_QUARTER, "fixed_weight_geomean_ppsm_nominal_sgd"].iloc[0])
    real_base = float(result.loc[result["quarter_id"] == START_QUARTER, "fixed_weight_geomean_ppsm_real_2024_sgd"].iloc[0])
    result["composition_adjusted_nominal_index"] = (
        result["fixed_weight_geomean_ppsm_nominal_sgd"] / nominal_base * 100.0
    )
    result["composition_adjusted_real_index"] = (
        result["fixed_weight_geomean_ppsm_real_2024_sgd"] / real_base * 100.0
    )
    result["composition_adjusted_nominal_yoy_pct"] = (
        result["composition_adjusted_nominal_index"].pct_change(4, fill_method=None) * 100.0
    )
    result["composition_adjusted_real_yoy_pct"] = (
        result["composition_adjusted_real_index"].pct_change(4, fill_method=None) * 100.0
    )
    metadata = {
        "stable_strata_n": int(len(stable_index)),
        "stable_main_period_transactions": int(len(stable_main)),
        "minimum_cell_n_each_quarter": MIN_CELL_N,
    }
    return result, metadata


def newey_west_trend(index_values: pd.Series, lags: int = HAC_LAGS) -> dict[str, float]:
    y = np.log(index_values.to_numpy(dtype=float) / 100.0)
    t = np.arange(len(y), dtype=float)
    x = np.column_stack([np.ones(len(y)), t])
    inv_xx = np.linalg.inv(x.T @ x)
    beta = inv_xx @ x.T @ y
    residual = y - x @ beta

    meat = np.zeros((2, 2), dtype=float)
    for i in range(len(y)):
        score = x[i] * residual[i]
        meat += np.outer(score, score)
    for lag in range(1, min(lags, len(y) - 1) + 1):
        bartlett = 1.0 - lag / (lags + 1.0)
        lag_sum = np.zeros((2, 2), dtype=float)
        for i in range(lag, len(y)):
            left = x[i] * residual[i]
            right = x[i - lag] * residual[i - lag]
            lag_sum += np.outer(left, right) + np.outer(right, left)
        meat += bartlett * lag_sum
    covariance = inv_xx @ meat @ inv_xx
    slope = float(beta[1])
    slope_se = float(math.sqrt(max(covariance[1, 1], 0.0)))
    z_value = slope / slope_se if slope_se > 0 else float("nan")
    p_value = math.erfc(abs(z_value) / math.sqrt(2.0)) if math.isfinite(z_value) else float("nan")
    low = slope - 1.96 * slope_se
    high = slope + 1.96 * slope_se
    return {
        "quarterly_log_slope": slope,
        "hac_se": slope_se,
        "z_normal_approx": z_value,
        "p_value_normal_approx": p_value,
        "annualized_trend_pct": (math.exp(slope * 4.0) - 1.0) * 100.0,
        "annualized_ci_low_pct": (math.exp(low * 4.0) - 1.0) * 100.0,
        "annualized_ci_high_pct": (math.exp(high * 4.0) - 1.0) * 100.0,
        "observations_quarters": int(len(y)),
        "hac_lags": int(lags),
    }


def pct_change_between(frame: pd.DataFrame, column: str, start: str, end: str) -> float:
    first = float(frame.loc[frame["quarter_id"] == start, column].iloc[0])
    last = float(frame.loc[frame["quarter_id"] == end, column].iloc[0])
    return (last / first - 1.0) * 100.0


def build_sensitivity(data: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    scenarios = [
        ("All queried transactions", data),
        ("Exclude potential duplicate flags", data[~data["potential_duplicate_flag"]].copy()),
    ]
    for label, frame in scenarios:
        adjusted, metadata = composition_adjusted_index(frame)
        rows.append(
            {
                "scenario": label,
                "queried_transactions": len(frame),
                "potential_duplicate_transactions": int(frame["potential_duplicate_flag"].sum()),
                "stable_strata_n": metadata["stable_strata_n"],
                "nominal_growth_2017Q1_to_2025Q4_pct": pct_change_between(
                    adjusted, "composition_adjusted_nominal_index", START_QUARTER, MAIN_END_QUARTER
                ),
                "real_growth_2017Q1_to_2025Q4_pct": pct_change_between(
                    adjusted, "composition_adjusted_real_index", START_QUARTER, MAIN_END_QUARTER
                ),
            }
        )
    result = pd.DataFrame(rows)
    baseline = float(result.loc[0, "real_growth_2017Q1_to_2025Q4_pct"])
    result["real_growth_difference_vs_all_pp"] = (
        result["real_growth_2017Q1_to_2025Q4_pct"] - baseline
    )
    return result


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "arialbd.ttf" if bold else "arial.ttf"
    windows_dir = os.environ.get("WINDIR")
    if windows_dir:
        candidate = Path(windows_dir) / "Fonts" / name
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default(size=size)


def draw_line_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    quarters: list[str],
    series: list[tuple[str, list[float], str]],
    title: str,
) -> None:
    left, top, right, bottom = box
    values = [value for _, points, _ in series for value in points]
    y_min = math.floor((min(values) - 7.0) / 10.0) * 10.0
    y_max = math.ceil((max(values) + 7.0) / 10.0) * 10.0
    if y_max <= y_min:
        y_max = y_min + 10.0

    draw.text((left, top - 65), title, fill=COLORS["text"], font=font(29, True))
    for tick in np.linspace(y_min, y_max, 6):
        y = bottom - (tick - y_min) / (y_max - y_min) * (bottom - top)
        draw.line((left, y, right, y), fill=COLORS["grid"], width=2)
        draw.text((left - 16, y), f"{tick:.0f}", anchor="rm", fill=COLORS["muted"], font=font(19))
    draw.line((left, top, left, bottom), fill=COLORS["axis"], width=2)
    draw.line((left, bottom, right, bottom), fill=COLORS["axis"], width=2)

    x_positions = np.linspace(left, right, len(quarters))
    for index, quarter in enumerate(quarters):
        if quarter.endswith("Q1") or quarter == quarters[-1]:
            draw.line((x_positions[index], bottom, x_positions[index], bottom + 8), fill=COLORS["axis"], width=2)
            label = quarter[:4] if quarter.endswith("Q1") else quarter
            draw.text((x_positions[index], bottom + 16), label, anchor="ma", fill=COLORS["muted"], font=font(18))

    for label, points, color in series:
        coords = []
        for x, value in zip(x_positions, points):
            y = bottom - (value - y_min) / (y_max - y_min) * (bottom - top)
            coords.append((float(x), float(y)))
        draw.line(coords, fill=color, width=7, joint="curve")
        for x, y in coords[::4]:
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=color)
        end_x, end_y = coords[-1]
        draw.text((end_x - 3, end_y - 13), f"{points[-1]:.1f}", anchor="rb", fill=color, font=font(22, True))

    legend_x = left + 15
    for label, _, color in series:
        draw.line((legend_x, top + 18, legend_x + 42, top + 18), fill=color, width=7)
        draw.text((legend_x + 55, top + 18), label, anchor="lm", fill=COLORS["text"], font=font(20))
        legend_x += 225


def market_index_chart(summary: pd.DataFrame, output: Path) -> None:
    plot = summary[summary["quarter_id"] <= MAIN_END_QUARTER].copy()
    quarters = plot["quarter_id"].tolist()
    image = Image.new("RGB", (1800, 1050), "white")
    draw = ImageDraw.Draw(image)
    draw.text((90, 52), "HDB resale prices before and after CPI adjustment", fill=COLORS["text"], font=font(42, True))
    draw.text(
        (90, 112),
        "Quarterly transaction medians; 2017 Q1 = 100; real values are expressed in 2024 SGD",
        fill=COLORS["muted"],
        font=font(25),
    )
    draw_line_panel(
        draw,
        (115, 255, 825, 870),
        quarters,
        [
            ("Nominal", plot["median_resale_price_nominal_sgd_index"].tolist(), COLORS["nominal"]),
            ("CPI-adjusted", plot["median_resale_price_real_2024_sgd_index"].tolist(), COLORS["real"]),
        ],
        "Median resale price index",
    )
    draw_line_panel(
        draw,
        (995, 255, 1705, 870),
        quarters,
        [
            ("Nominal", plot["median_price_per_sqm_nominal_sgd_index"].tolist(), COLORS["nominal"]),
            ("CPI-adjusted", plot["median_price_per_sqm_real_2024_sgd_index"].tolist(), COLORS["real"]),
        ],
        "Median price per sqm index",
    )
    draw.text(
        (90, 980),
        "Source: SQL query of fact_resale_transaction. Main comparison ends at 2025 Q4 to use complete years.",
        fill=COLORS["muted"],
        font=font(20),
    )
    image.save(output, dpi=(180, 180))


def adjusted_index_chart(adjusted: pd.DataFrame, endpoint: dict[str, float], output: Path) -> None:
    plot = adjusted[adjusted["quarter_id"] <= MAIN_END_QUARTER].copy()
    image = Image.new("RGB", (1800, 1050), "white")
    draw = ImageDraw.Draw(image)
    draw.text((90, 52), "CPI explains part, but not all, of the HDB price increase", fill=COLORS["text"], font=font(42, True))
    draw.text(
        (90, 112),
        "Fixed-weight Town x flat-type price-per-sqm index; stable strata only; 2017 Q1 = 100",
        fill=COLORS["muted"],
        font=font(25),
    )
    draw_line_panel(
        draw,
        (125, 260, 1235, 870),
        plot["quarter_id"].tolist(),
        [
            ("Nominal", plot["composition_adjusted_nominal_index"].tolist(), COLORS["nominal"]),
            ("CPI-adjusted", plot["composition_adjusted_real_index"].tolist(), COLORS["real"]),
        ],
        "Composition-adjusted price-per-sqm index",
    )

    panel_x = 1325
    draw.text((panel_x, 260), "2017 Q1 to 2025 Q4", fill=COLORS["text"], font=font(26, True))
    draw.text((panel_x, 335), "Nominal growth", fill=COLORS["muted"], font=font(22))
    draw.text((panel_x, 377), f"+{endpoint['adjusted_nominal_growth_pct']:.1f}%", fill=COLORS["nominal"], font=font(39, True))
    draw.text((panel_x, 470), "Real growth", fill=COLORS["muted"], font=font(22))
    draw.text((panel_x, 512), f"+{endpoint['adjusted_real_growth_pct']:.1f}%", fill=COLORS["real"], font=font(39, True))
    draw.text((panel_x, 605), "Cumulative CPI rise", fill=COLORS["muted"], font=font(22))
    draw.text((panel_x, 647), f"+{endpoint['cpi_change_pct']:.1f}%", fill=COLORS["accent"], font=font(39, True))
    draw.text((panel_x, 755), "Conclusion", fill=COLORS["text"], font=font(24, True))
    draw.multiline_text(
        (panel_x, 800),
        "The real increase remains\nsubstantial, although much\nsmaller than the nominal rise.",
        fill=COLORS["text"],
        font=font(23),
        spacing=8,
    )
    draw.text(
        (90, 980),
        "Stable stratum rule: at least 10 sales in every quarter of 2017-2025. Weights are pooled-period transaction shares.",
        fill=COLORS["muted"],
        font=font(20),
    )
    image.save(output, dpi=(180, 180))


def draw_bar_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    labels: list[str],
    values: list[float],
    title: str,
    color: str,
) -> None:
    left, top, right, bottom = box
    y_max = math.ceil(max(values) * 1.15 / 5000.0) * 5000.0
    draw.text((left, top - 65), title, fill=COLORS["text"], font=font(29, True))
    for tick in np.linspace(0, y_max, 5):
        y = bottom - tick / y_max * (bottom - top)
        draw.line((left, y, right, y), fill=COLORS["grid"], width=2)
        draw.text((left - 16, y), f"{tick / 1000:.0f}k", anchor="rm", fill=COLORS["muted"], font=font(19))
    draw.line((left, top, left, bottom), fill=COLORS["axis"], width=2)
    draw.line((left, bottom, right, bottom), fill=COLORS["axis"], width=2)

    positions = np.linspace(left + 42, right - 42, len(values))
    width = min(54, int((right - left) / (len(values) * 1.55)))
    for x, label, value in zip(positions, labels, values):
        y = bottom - value / y_max * (bottom - top)
        draw.rounded_rectangle((x - width / 2, y, x + width / 2, bottom), radius=8, fill=color)
        draw.text((x, y - 12), f"{value / 1000:.1f}k", anchor="ms", fill=COLORS["text"], font=font(18, True))
        draw.text((x, bottom + 17), label, anchor="ma", fill=COLORS["muted"], font=font(18))


def annual_market_chart(annual: pd.DataFrame, output: Path) -> None:
    plot = annual.copy()
    years = [str(int(year)) for year in plot["year"]]
    image = Image.new("RGB", (1800, 1050), "white")
    draw = ImageDraw.Draw(image)
    draw.text((90, 52), "Annual HDB resale-market summary", fill=COLORS["text"], font=font(42, True))
    draw.text(
        (90, 112),
        "Price-per-sqm changes are shown relative to 2017; annual transaction counts provide market context",
        fill=COLORS["muted"],
        font=font(25),
    )
    draw_line_panel(
        draw,
        (115, 255, 855, 855),
        years,
        [
            ("Nominal", (100.0 + plot["median_price_per_sqm_nominal_sgd_vs_2017_pct"]).tolist(), COLORS["nominal"]),
            ("CPI-adjusted", (100.0 + plot["median_price_per_sqm_real_2024_sgd_vs_2017_pct"]).tolist(), COLORS["real"]),
            ("CPI", (100.0 + plot["annual_cpi_all_items_2024_base_vs_2017_pct"]).tolist(), COLORS["accent"]),
        ],
        "Annual median price-per-sqm index",
    )
    draw_bar_panel(
        draw,
        (1045, 255, 1705, 855),
        years,
        plot["transaction_n"].astype(float).tolist(),
        "Annual transactions",
        COLORS["nominal"],
    )
    draw.text(
        (90, 980),
        "Source: SQL query of fact_resale_transaction. Main comparison uses the complete years 2017-2025.",
        fill=COLORS["muted"],
        font=font(20),
    )
    image.save(output, dpi=(180, 180))


def cpi_context_chart(quarterly: pd.DataFrame, adjusted: pd.DataFrame, output: Path) -> None:
    prices = adjusted[adjusted["quarter_id"] <= MAIN_END_QUARTER].copy()
    cpi = quarterly.loc[
        quarterly["quarter_id"] <= MAIN_END_QUARTER,
        ["quarter_id", "quarter_cpi_all_items_2024_base"],
    ].copy()
    plot = prices.merge(cpi, on="quarter_id", how="inner", validate="one_to_one")
    cpi_index = plot["quarter_cpi_all_items_2024_base"] / plot["quarter_cpi_all_items_2024_base"].iloc[0] * 100.0

    image = Image.new("RGB", (1800, 1050), "white")
    draw = ImageDraw.Draw(image)
    draw.text((90, 52), "CPI and composition-adjusted HDB price trends", fill=COLORS["text"], font=font(42, True))
    draw.text(
        (90, 112),
        "All series are indexed to 2017 Q1 = 100; the CPI series shows the inflation adjustment applied to prices",
        fill=COLORS["muted"],
        font=font(25),
    )
    draw_line_panel(
        draw,
        (130, 260, 1670, 850),
        plot["quarter_id"].tolist(),
        [
            ("Nominal price", plot["composition_adjusted_nominal_index"].tolist(), COLORS["nominal"]),
            ("Real price", plot["composition_adjusted_real_index"].tolist(), COLORS["real"]),
            ("CPI", cpi_index.tolist(), COLORS["accent"]),
        ],
        "Fixed-weight Town x flat-type price-per-sqm and CPI indices",
    )
    draw.text(
        (90, 980),
        "The gap between nominal and CPI-adjusted prices quantifies the portion of the nominal increase explained by inflation.",
        fill=COLORS["muted"],
        font=font(20),
    )
    image.save(output, dpi=(180, 180))


def duplicate_sensitivity_chart(sensitivity: pd.DataFrame, output: Path) -> None:
    all_sales = sensitivity.iloc[0]
    excluded = sensitivity.iloc[1]
    groups = [
        ("All queried sales", all_sales),
        ("Exclude duplicate flags", excluded),
    ]
    image = Image.new("RGB", (1800, 1050), "white")
    draw = ImageDraw.Draw(image)
    draw.text((90, 52), "Duplicate-flag sensitivity check", fill=COLORS["text"], font=font(42, True))
    draw.text(
        (90, 112),
        "Growth in the fixed-weight price-per-sqm index, 2017 Q1 to 2025 Q4",
        fill=COLORS["muted"],
        font=font(25),
    )

    left, top, right, bottom = 165, 260, 1670, 820
    y_max = 60.0
    for tick in range(0, 61, 10):
        y = bottom - tick / y_max * (bottom - top)
        draw.line((left, y, right, y), fill=COLORS["grid"], width=2)
        draw.text((left - 18, y), f"{tick}%", anchor="rm", fill=COLORS["muted"], font=font(20))
    draw.line((left, top, left, bottom), fill=COLORS["axis"], width=2)
    draw.line((left, bottom, right, bottom), fill=COLORS["axis"], width=2)

    group_centres = [590, 1235]
    legend = [("Nominal growth", COLORS["nominal"]), ("CPI-adjusted growth", COLORS["real"])]
    legend_x = 555
    for label, color in legend:
        draw.rectangle((legend_x, 190, legend_x + 35, 215), fill=color)
        draw.text((legend_x + 48, 202), label, anchor="lm", fill=COLORS["text"], font=font(21))
        legend_x += 290

    for centre, (label, row) in zip(group_centres, groups):
        bars = [
            (centre - 118, float(row["nominal_growth_2017Q1_to_2025Q4_pct"]), COLORS["nominal"]),
            (centre + 28, float(row["real_growth_2017Q1_to_2025Q4_pct"]), COLORS["real"]),
        ]
        for x, value, color in bars:
            y = bottom - value / y_max * (bottom - top)
            draw.rounded_rectangle((x, y, x + 110, bottom), radius=9, fill=color)
            draw.text((x + 55, y - 16), f"{value:.1f}%", anchor="ms", fill=COLORS["text"], font=font(23, True))
        draw.multiline_text((centre, bottom + 35), label, anchor="ma", align="center", fill=COLORS["muted"], font=font(22), spacing=5)

    flagged = int(all_sales["potential_duplicate_transactions"])
    difference = float(excluded["real_growth_difference_vs_all_pp"])
    draw.text(
        (90, 960),
        f"Removing {flagged:,} potential duplicate-flag transactions changes CPI-adjusted growth by only {difference:+.2f} percentage points.",
        fill=COLORS["muted"],
        font=font(21),
    )
    image.save(output, dpi=(180, 180))


def make_endpoint_summary(
    quarterly: pd.DataFrame,
    adjusted: pd.DataFrame,
    trend: dict[str, float],
) -> dict[str, Any]:
    nominal_adjusted_growth = pct_change_between(
        adjusted, "composition_adjusted_nominal_index", START_QUARTER, MAIN_END_QUARTER
    )
    real_adjusted_growth = pct_change_between(
        adjusted, "composition_adjusted_real_index", START_QUARTER, MAIN_END_QUARTER
    )
    start_cpi = float(
        quarterly.loc[quarterly["quarter_id"] == START_QUARTER, "quarter_cpi_all_items_2024_base"].iloc[0]
    )
    end_cpi = float(
        quarterly.loc[quarterly["quarter_id"] == MAIN_END_QUARTER, "quarter_cpi_all_items_2024_base"].iloc[0]
    )
    cpi_change = (end_cpi / start_cpi - 1.0) * 100.0
    years = quarter_distance(START_QUARTER, MAIN_END_QUARTER) / 4.0
    nominal_index = 1.0 + nominal_adjusted_growth / 100.0
    real_index = 1.0 + real_adjusted_growth / 100.0
    share_remaining = math.log(real_index) / math.log(nominal_index) * 100.0

    result: dict[str, Any] = {
        "main_period": f"{START_QUARTER} to {MAIN_END_QUARTER}",
        "latest_supplement_end": LATEST_END_QUARTER,
        "market_median_resale_nominal_growth_pct": pct_change_between(
            quarterly, "median_resale_price_nominal_sgd_index", START_QUARTER, MAIN_END_QUARTER
        ),
        "market_median_resale_real_growth_pct": pct_change_between(
            quarterly, "median_resale_price_real_2024_sgd_index", START_QUARTER, MAIN_END_QUARTER
        ),
        "market_median_ppsm_nominal_growth_pct": pct_change_between(
            quarterly, "median_price_per_sqm_nominal_sgd_index", START_QUARTER, MAIN_END_QUARTER
        ),
        "market_median_ppsm_real_growth_pct": pct_change_between(
            quarterly, "median_price_per_sqm_real_2024_sgd_index", START_QUARTER, MAIN_END_QUARTER
        ),
        "adjusted_nominal_growth_pct": nominal_adjusted_growth,
        "adjusted_real_growth_pct": real_adjusted_growth,
        "cpi_change_pct": cpi_change,
        "adjusted_nominal_cagr_pct": (nominal_index ** (1.0 / years) - 1.0) * 100.0,
        "adjusted_real_cagr_pct": (real_index ** (1.0 / years) - 1.0) * 100.0,
        "share_of_log_nominal_growth_remaining_after_cpi_pct": share_remaining,
        "latest_2026Q2_adjusted_nominal_growth_since_2017Q1_pct": pct_change_between(
            adjusted, "composition_adjusted_nominal_index", START_QUARTER, LATEST_END_QUARTER
        ),
        "latest_2026Q2_adjusted_real_growth_since_2017Q1_pct": pct_change_between(
            adjusted, "composition_adjusted_real_index", START_QUARTER, LATEST_END_QUARTER
        ),
        "real_trend_newey_west": trend,
        "answer": (
            "Yes. The CPI-adjusted increase remains clear, but inflation explains a material "
            "part of the nominal increase. This is a descriptive market trend, not a causal claim."
        ),
    }
    return result


def write_slide_notes(summary: dict[str, Any], metadata: dict[str, int], output: Path) -> None:
    trend = summary["real_trend_newey_west"]
    text = f"""RQ4 转售价格的变化在扣除 CPI 通胀后是否仍然明显

结论
是。扣除 CPI 后价格上涨仍然清晰，但幅度明显低于名义涨幅。

核心数字 2017 Q1 至 2025 Q4
- 固定 Town×户型构成后的每平方米名义价格指数上涨 {summary['adjusted_nominal_growth_pct']:.1f}%。
- 同一指数扣除 CPI 后仍上涨 {summary['adjusted_real_growth_pct']:.1f}%。
- 同期 CPI 上涨 {summary['cpi_change_pct']:.1f}%。
- 按对数涨幅计算，名义涨幅中约 {summary['share_of_log_nominal_growth_remaining_after_cpi_pct']:.1f}% 在扣除通胀后仍保留。
- 实际指数的季度趋势折算年化为 {trend['annualized_trend_pct']:.1f}%，Newey-West 95% 区间为 {trend['annualized_ci_low_pct']:.1f}% 至 {trend['annualized_ci_high_pct']:.1f}%。

方法
- Python 只通过 SQL 从 hdb_project 数据库读取交易数据。
- 主比较期使用完整年度 2017-2025；2026 Q2 仅作最新补充。
- 名义价与 2024 年不变价同时以 2017 Q1=100 指数化。
- 为减少成交结构变化的干扰，保留 2017-2025 每季度至少 10 笔成交的 Town×户型组合，共 {metadata['stable_strata_n']} 组，并固定其权重。
- 结论描述趋势和关联，不声称 CPI 或其他变量导致价格变化。
"""
    output.write_text(text, encoding="utf-8-sig")


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    data, database_label = fetch_data(args)
    quarterly = quarterly_summary(data)
    annual = annual_summary(data)
    adjusted, metadata = composition_adjusted_index(data)
    sensitivity = build_sensitivity(data)

    main_real_index = adjusted.loc[
        adjusted["quarter_id"] <= MAIN_END_QUARTER,
        "composition_adjusted_real_index",
    ]
    trend = newey_west_trend(main_real_index)
    endpoint = make_endpoint_summary(quarterly, adjusted, trend)

    quarterly.to_csv(output_dir / "quarterly_price_summary.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(output_dir / "annual_price_summary.csv", index=False, encoding="utf-8-sig")
    adjusted.to_csv(output_dir / "composition_adjusted_index.csv", index=False, encoding="utf-8-sig")
    sensitivity.to_csv(output_dir / "duplicate_sensitivity.csv", index=False, encoding="utf-8-sig")

    market_index_chart(quarterly, output_dir / "rq4_nominal_vs_real_index.png")
    adjusted_index_chart(adjusted, endpoint, output_dir / "rq4_composition_adjusted_index.png")
    annual_market_chart(annual, output_dir / "rq4_annual_market_summary.png")
    cpi_context_chart(quarterly, adjusted, output_dir / "rq4_cpi_price_context.png")
    duplicate_sensitivity_chart(sensitivity, output_dir / "rq4_duplicate_sensitivity.png")
    legacy_notes = output_dir / "PPT_NOTES_zh.txt"
    if legacy_notes.exists():
        legacy_notes.unlink()

    summary = {
        "database_source": database_label,
        "sql_only_analysis_input": True,
        "queried_transactions": int(len(data)),
        "queried_quarters": int(data["quarter_id"].nunique()),
        "potential_duplicate_flags": int(data["potential_duplicate_flag"].sum()),
        "composition_adjustment": metadata,
        **endpoint,
        "duplicate_sensitivity_real_growth_difference_pp": float(
            sensitivity.loc[1, "real_growth_difference_vs_all_pp"]
        ),
    }
    (output_dir / "analysis_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nOutputs saved to: {output_dir}")


if __name__ == "__main__":
    main()
