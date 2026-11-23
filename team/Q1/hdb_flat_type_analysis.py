"""Exploratory analysis of HDB resale price trends by flat type.

The script queries MySQL directly. It does not read the processed CSV files.
It creates quarterly summaries and publication-ready figures beside this file.

Install dependencies in the VS Code terminal:
    python -m pip install mysql-connector-python pandas matplotlib numpy

Run:
    python hdb_flat_type_analysis.py
"""

from __future__ import annotations

import getpass
import math
from pathlib import Path

import matplotlib.pyplot as plt
import mysql.connector
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Connection settings. Do not put your password in this file.
# ---------------------------------------------------------------------------
DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "database": "hdb_project",
}

START_QUARTER = "2017-Q1"
END_QUARTER = "2026-Q2"

# A quarterly group below this threshold is omitted from trend lines to reduce
# noise from very small samples. All counts remain visible in the sample chart
# and exported tables. Change this value only with a clear reason.
MIN_QUARTERLY_N_FOR_TREND = 30

OUTPUT_DIR = Path(__file__).resolve().parent / "hdb_flat_type_analysis_output"


# This query returns transaction-level observations from the database. Python
# calculates robust quarterly statistics from these query results.
SQL = """
SELECT
    r.resale_id,
    r.quarter_id,
    f.flat_type,
    t.town_name,
    r.resale_price_nominal_sgd,
    r.price_per_sqm_nominal_sgd,
    r.potential_duplicate_flag
FROM fact_resale_transaction AS r
JOIN dim_flat_type AS f
    ON r.flat_type_id = f.flat_type_id
JOIN dim_town AS t
    ON r.town_id = t.town_id
JOIN dim_time_month AS tm
    ON r.month_id = tm.month_id
WHERE tm.complete_quarter_flag = TRUE
  AND r.quarter_id >= %s
  AND r.quarter_id <= %s
  AND r.resale_price_nominal_sgd > 0
  AND r.price_per_sqm_nominal_sgd > 0
ORDER BY r.quarter_id, f.flat_type
"""


METRICS = {
    "resale_price_nominal_sgd": {
        "median": "median_price_sgd",
        "q25": "price_q25_sgd",
        "q75": "price_q75_sgd",
        "mean": "mean_price_sgd",
        "title": "Quarterly resale price by flat type",
        "ylabel": "Resale price (SGD, nominal)",
        "index": "resale_price_index",
        "index_title": "Resale price trend by flat type (indexed)",
        "index_ylabel": "Price index (first reliable quarter = 100)",
    },
    "price_per_sqm_nominal_sgd": {
        "median": "median_price_per_sqm_sgd",
        "q25": "price_per_sqm_q25_sgd",
        "q75": "price_per_sqm_q75_sgd",
        "mean": "mean_price_per_sqm_sgd",
        "title": "Quarterly price per square metre by flat type",
        "ylabel": "Price per square metre (SGD, nominal)",
        "index": "price_per_sqm_index",
        "index_title": "Price per square metre trend by flat type (indexed)",
        "index_ylabel": "Price per sqm index (first reliable quarter = 100)",
    },
}


def quarter_sort_key(value: str) -> tuple[int, int]:
    """Return a chronological sort key for a quarter such as 2024-Q3."""
    year_text, quarter_text = value.split("-Q")
    return int(year_text), int(quarter_text)


def quarter_to_timestamp(value: str) -> pd.Timestamp:
    """Return the first day of the calendar quarter."""
    year_text, quarter_text = value.split("-Q")
    month = (int(quarter_text) - 1) * 3 + 1
    return pd.Timestamp(year=int(year_text), month=month, day=1)


def fetch_transactions() -> pd.DataFrame:
    """Fetch the selected transaction records directly from MySQL."""
    password = getpass.getpass("MySQL root password: ")
    connection = mysql.connector.connect(password=password, **DB_CONFIG)

    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(SQL, (START_QUARTER, END_QUARTER))
        records = cursor.fetchall()
        cursor.close()
    finally:
        connection.close()

    data = pd.DataFrame(records)
    if data.empty:
        raise RuntimeError(
            "The database query returned no rows. Check the database name, "
            "table contents, and quarter range."
        )

    numeric_columns = [
        "resale_price_nominal_sgd",
        "price_per_sqm_nominal_sgd",
    ]
    for column in numeric_columns:
        data[column] = pd.to_numeric(data[column], errors="coerce")

    data["quarter_id"] = data["quarter_id"].astype(str)
    data["potential_duplicate_flag"] = (
        data["potential_duplicate_flag"].fillna(False).astype(bool)
    )
    data = data.dropna(subset=numeric_columns + ["quarter_id", "flat_type"])
    return data


def summarize_by_quarter(data: pd.DataFrame, quarter_order: list[str], flat_types: list[str]) -> pd.DataFrame:
    """Calculate medians, means, IQRs, sample counts, and Town coverage."""
    summary = (
        data.groupby(["quarter_id", "flat_type"], as_index=False, observed=True)
        .agg(
            transaction_n=("resale_id", "size"),
            town_n=("town_name", "nunique"),
            median_price_sgd=("resale_price_nominal_sgd", "median"),
            price_q25_sgd=("resale_price_nominal_sgd", lambda values: values.quantile(0.25)),
            price_q75_sgd=("resale_price_nominal_sgd", lambda values: values.quantile(0.75)),
            mean_price_sgd=("resale_price_nominal_sgd", "mean"),
            median_price_per_sqm_sgd=("price_per_sqm_nominal_sgd", "median"),
            price_per_sqm_q25_sgd=("price_per_sqm_nominal_sgd", lambda values: values.quantile(0.25)),
            price_per_sqm_q75_sgd=("price_per_sqm_nominal_sgd", lambda values: values.quantile(0.75)),
            mean_price_per_sqm_sgd=("price_per_sqm_nominal_sgd", "mean"),
        )
    )

    # Keep missing quarter/type combinations explicit so that year-over-year
    # changes compare the same quarter exactly four quarters apart.
    complete_grid = pd.MultiIndex.from_product(
        [quarter_order, flat_types], names=["quarter_id", "flat_type"]
    )
    summary = (
        summary.set_index(["quarter_id", "flat_type"])
        .reindex(complete_grid)
        .reset_index()
    )
    summary["transaction_n"] = summary["transaction_n"].fillna(0).astype(int)
    summary["town_n"] = summary["town_n"].fillna(0).astype(int)
    summary["quarter_date"] = summary["quarter_id"].map(quarter_to_timestamp)
    summary["low_sample_flag"] = summary["transaction_n"] < MIN_QUARTERLY_N_FOR_TREND

    for metric in ["median_price_sgd", "median_price_per_sqm_sgd"]:
        yoy_name = f"{metric}_yoy_pct"
        summary[yoy_name] = summary.groupby("flat_type", sort=False)[metric].transform(
            lambda values: values.pct_change(periods=4, fill_method=None) * 100
        )

    return summary


def add_indices(summary: pd.DataFrame) -> pd.DataFrame:
    """Index each type to its first quarter with a sufficiently large sample."""
    result = summary.copy()
    for metric in ["median_price_sgd", "median_price_per_sqm_sgd"]:
        index_column = (
            "resale_price_index" if metric == "median_price_sgd" else "price_per_sqm_index"
        )
        result[index_column] = float("nan")

        for _, group in result.groupby("flat_type", sort=False):
            valid = group[
                (group["transaction_n"] >= MIN_QUARTERLY_N_FOR_TREND)
                & group[metric].notna()
            ]
            if valid.empty:
                continue
            baseline = valid[metric].iloc[0]
            if baseline == 0:
                continue
            result.loc[valid.index, index_column] = valid[metric] / baseline * 100

    return result


def save_facet_chart(
    summary: pd.DataFrame,
    metric: dict[str, str],
    output_path: Path,
    quarter_order: list[str],
) -> None:
    """Plot only flat types with at least one reliable quarterly group."""
    eligible_types = sorted(
        summary.loc[
            summary["transaction_n"] >= MIN_QUARTERLY_N_FOR_TREND, "flat_type"
        ].unique()
    )
    excluded_types = sorted(set(summary["flat_type"].unique()) - set(eligible_types))
    if not eligible_types:
        raise RuntimeError("No flat type meets the quarterly sample threshold.")

    columns = 2
    rows = math.ceil(len(eligible_types) / columns)
    fig, axes = plt.subplots(
        rows, columns, figsize=(15, 3.8 * rows), sharex=True, squeeze=False
    )
    axes = list(axes.ravel())

    for axis, flat_type in zip(axes, eligible_types):
        group = summary[
            (summary["flat_type"] == flat_type)
            & (summary["transaction_n"] >= MIN_QUARTERLY_N_FOR_TREND)
        ].sort_values("quarter_date")

        if not group.empty:
            axis.fill_between(
                group["quarter_date"],
                group[metric["q25"]],
                group[metric["q75"]],
                color="#9bb8d3",
                alpha=0.35,
                linewidth=0,
                label="25th–75th percentile",
            )
            axis.plot(
                group["quarter_date"],
                group[metric["median"]],
                color="#173f5f",
                linewidth=2.2,
                label="Quarterly median",
            )
        axis.set_title(flat_type.replace("_", " "))
        axis.set_ylabel(metric["ylabel"])
        axis.grid(axis="y", color="#d9dee5", linewidth=0.7)
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)
        axis.legend(frameon=False, loc="upper left", fontsize=8)

    for axis in axes[len(eligible_types):]:
        axis.remove()

    tick_quarters = quarter_order[::4]
    tick_dates = [quarter_to_timestamp(value) for value in tick_quarters]
    for axis in axes[:len(eligible_types)]:
        axis.set_xticks(tick_dates)
        axis.set_xticklabels(tick_quarters, rotation=35, ha="right")
        axis.set_xlabel("Quarter")

    fig.suptitle(
        metric["title"] + "\nMedian line; shaded band is the middle 50% of transactions",
        fontsize=16,
        y=1.01,
    )
    excluded_note = ""
    if excluded_types:
        excluded_note = " | Not plotted (no quarter meets n threshold): " + ", ".join(
            name.replace("_", " ") for name in excluded_types
        )
    fig.text(
        0.01,
        0.005,
        f"Source: hdb_project.fact_resale_transaction | {START_QUARTER} to {END_QUARTER} | "
        f"Complete quarters only; n >= {MIN_QUARTERLY_N_FOR_TREND} per quarter"
        f"{excluded_note} | Shaded band is the IQR, not a confidence interval",
        fontsize=9,
        color="#4b5563",
    )
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    fig.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def save_index_chart(
    summary: pd.DataFrame,
    metric: dict[str, str],
    output_path: Path,
    quarter_order: list[str],
) -> None:
    """Save an indexed chart to compare relative growth across types."""
    index_column = metric["index"]
    fig, axis = plt.subplots(figsize=(14, 7))

    for flat_type in sorted(summary["flat_type"].unique()):
        group = summary[
            (summary["flat_type"] == flat_type)
            & (summary["transaction_n"] >= MIN_QUARTERLY_N_FOR_TREND)
        ].sort_values("quarter_date")
        group = group.dropna(subset=[index_column])
        if not group.empty:
            axis.plot(
                group["quarter_date"],
                group[index_column],
                linewidth=2,
                label=flat_type.replace("_", " "),
            )

    axis.axhline(100, color="#6b7280", linestyle="--", linewidth=1)
    axis.set_title(metric["index_title"], fontsize=16)
    axis.set_ylabel(metric["index_ylabel"])
    axis.set_xlabel("Quarter")
    axis.grid(axis="y", color="#d9dee5", linewidth=0.7)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.legend(frameon=False, ncol=3, loc="upper left")

    tick_quarters = quarter_order[::4]
    axis.set_xticks([quarter_to_timestamp(value) for value in tick_quarters])
    axis.set_xticklabels(tick_quarters, rotation=35, ha="right")
    fig.text(
        0.01,
        0.005,
        f"Source: hdb_project.fact_resale_transaction | Each type is indexed to its first quarter with "
        f"n >= {MIN_QUARTERLY_N_FOR_TREND}; baselines may differ by type",
        fontsize=9,
        color="#4b5563",
    )
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def save_sample_heatmap(summary: pd.DataFrame, output_path: Path, quarter_order: list[str]) -> None:
    """Save a heatmap with exact counts printed in each cell."""
    table = summary.pivot(index="quarter_id", columns="flat_type", values="transaction_n")
    table = table.reindex(quarter_order)
    table = table.reindex(sorted(table.columns), axis=1).fillna(0)

    values = table.to_numpy(dtype=float)
    color_values = np.log10(values + 1)

    fig_height = max(6, 0.28 * len(table.index))
    fig, axis = plt.subplots(figsize=(11, fig_height))
    image = axis.imshow(color_values, aspect="auto", cmap="Blues", interpolation="nearest")
    axis.set_title("Quarterly resale transaction counts by flat type", fontsize=16)
    axis.set_xlabel("Flat type")
    axis.set_ylabel("Quarter")
    axis.set_xticks(range(len(table.columns)))
    axis.set_xticklabels([name.replace("_", " ") for name in table.columns], rotation=25, ha="right")

    # Print actual transaction counts so readers do not have to infer them from
    # the logarithmic color scale. White text keeps large-count cells legible.
    for row in range(values.shape[0]):
        for column in range(values.shape[1]):
            color = "white" if color_values[row, column] >= 2.2 else "#17324d"
            axis.text(
                column,
                row,
                f"{int(values[row, column]):,}",
                ha="center",
                va="center",
                fontsize=5.5,
                color=color,
            )

    y_positions = list(range(0, len(table.index), 4))
    axis.set_yticks(y_positions)
    axis.set_yticklabels([table.index[position] for position in y_positions])
    colorbar = fig.colorbar(image, ax=axis, pad=0.02)
    colorbar.set_label("log10(transaction count + 1)")
    fig.text(
        0.01,
        0.005,
        f"Source: hdb_project.fact_resale_transaction | {START_QUARTER} to {END_QUARTER} | "
        "Color scale is logarithmic",
        fontsize=9,
        color="#4b5563",
    )
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def duplicate_sensitivity(data: pd.DataFrame, summary_all: pd.DataFrame) -> pd.DataFrame:
    """Compare quarterly medians with and without exact-duplicate flags."""
    cleaned = data[~data["potential_duplicate_flag"]]
    summary_clean = summarize_by_quarter(
        cleaned,
        sorted(data["quarter_id"].unique(), key=quarter_sort_key),
        sorted(data["flat_type"].unique()),
    )

    columns = ["quarter_id", "flat_type", "median_price_sgd", "median_price_per_sqm_sgd"]
    comparison = summary_all[columns].merge(
        summary_clean[columns],
        on=["quarter_id", "flat_type"],
        how="left",
        suffixes=("_all", "_excluding_flagged"),
    )
    comparison["price_median_change_pct"] = (
        comparison["median_price_sgd_excluding_flagged"]
        / comparison["median_price_sgd_all"]
        - 1
    ) * 100
    comparison["price_per_sqm_median_change_pct"] = (
        comparison["median_price_per_sqm_sgd_excluding_flagged"]
        / comparison["median_price_per_sqm_sgd_all"]
        - 1
    ) * 100
    return comparison


def make_trend_overview(summary: pd.DataFrame, quarter_order: list[str]) -> pd.DataFrame:
    """Summarize first-to-last changes for reliable quarterly groups."""
    rows: list[dict[str, object]] = []
    for flat_type in sorted(summary["flat_type"].unique()):
        group = summary[
            (summary["flat_type"] == flat_type)
            & (summary["transaction_n"] >= MIN_QUARTERLY_N_FOR_TREND)
        ].sort_values("quarter_date")

        if group.empty:
            continue

        first = group.iloc[0]
        last = group.iloc[-1]
        quarter_span = quarter_order.index(last["quarter_id"]) - quarter_order.index(first["quarter_id"])
        years = quarter_span / 4

        row: dict[str, object] = {
            "flat_type": flat_type,
            "first_reliable_quarter": first["quarter_id"],
            "last_reliable_quarter": last["quarter_id"],
            "first_quarter_n": int(first["transaction_n"]),
            "last_quarter_n": int(last["transaction_n"]),
        }

        for column, output_prefix in [
            ("median_price_sgd", "resale_price"),
            ("median_price_per_sqm_sgd", "price_per_sqm"),
        ]:
            start_value = first[column]
            end_value = last[column]
            if pd.notna(start_value) and start_value != 0 and pd.notna(end_value):
                total_change = (end_value / start_value - 1) * 100
                annualized = ((end_value / start_value) ** (1 / years) - 1) * 100 if years > 0 else float("nan")
            else:
                total_change = float("nan")
                annualized = float("nan")
            row[f"{output_prefix}_first_median"] = start_value
            row[f"{output_prefix}_last_median"] = end_value
            row[f"{output_prefix}_total_change_pct"] = total_change
            row[f"{output_prefix}_annualized_change_pct"] = annualized

        rows.append(row)

    return pd.DataFrame(rows)


def make_sample_threshold_summary(summary: pd.DataFrame) -> pd.DataFrame:
    """Report total and quarterly sample sizes, including low-volume types."""
    result = (
        summary.groupby("flat_type", as_index=False, observed=True)
        .agg(
            total_transactions=("transaction_n", "sum"),
            max_quarterly_transactions=("transaction_n", "max"),
            quarters_with_transactions=("transaction_n", lambda values: int((values > 0).sum())),
            quarters_meeting_trend_threshold=(
                "transaction_n",
                lambda values: int((values >= MIN_QUARTERLY_N_FOR_TREND).sum()),
            ),
        )
        .sort_values("flat_type")
    )
    result["included_in_trend_charts"] = result["quarters_meeting_trend_threshold"] > 0
    return result


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "xtick.labelsize": 8,
            "ytick.labelsize": 9,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
        }
    )

    data = fetch_transactions()
    quarters = sorted(data["quarter_id"].unique(), key=quarter_sort_key)
    flat_types = sorted(data["flat_type"].unique())

    summary = summarize_by_quarter(data, quarters, flat_types)
    summary = add_indices(summary)
    sensitivity = duplicate_sensitivity(data, summary)
    overview = make_trend_overview(summary, quarters)
    sample_threshold = make_sample_threshold_summary(summary)

    summary.to_csv(OUTPUT_DIR / "quarterly_flat_type_summary.csv", index=False)
    sensitivity.to_csv(OUTPUT_DIR / "duplicate_flag_sensitivity.csv", index=False)
    overview.to_csv(OUTPUT_DIR / "flat_type_trend_overview.csv", index=False)
    sample_threshold.to_csv(OUTPUT_DIR / "sample_threshold_summary.csv", index=False)

    for metric in METRICS.values():
        file_stem = metric["index"]
        save_facet_chart(
            summary,
            metric,
            OUTPUT_DIR / f"{file_stem.replace('_index', '')}_by_type.png",
            quarters,
        )
        save_index_chart(
            summary,
            metric,
            OUTPUT_DIR / f"{file_stem}_by_type.png",
            quarters,
        )

    save_sample_heatmap(summary, OUTPUT_DIR / "quarterly_transaction_counts.png", quarters)

    print("\nAnalysis complete")
    print(f"Rows retrieved from MySQL: {len(data):,}")
    print(f"Quarter range: {quarters[0]} to {quarters[-1]}")
    print(f"Flat types: {', '.join(flat_types)}")
    print(
        "Potential-duplicate-flagged rows: "
        f"{int(data['potential_duplicate_flag'].sum()):,} "
        f"({data['potential_duplicate_flag'].mean() * 100:.2f}%)"
    )
    print(f"Minimum quarterly sample used in trend charts: {MIN_QUARTERLY_N_FOR_TREND}")
    print(f"\nFiles saved to: {OUTPUT_DIR}")
    print("- quarterly_flat_type_summary.csv")
    print("- flat_type_trend_overview.csv")
    print("- sample_threshold_summary.csv")
    print("- duplicate_flag_sensitivity.csv")
    print("- resale_price_by_type.png (quarterly medians and IQR)")
    print("- resale_price_index_by_type.png (relative trend index)")
    print("- price_per_sqm_by_type.png (quarterly medians and IQR)")
    print("- price_per_sqm_index_by_type.png")
    print("- quarterly_transaction_counts.png")
    print("\nTrend overview (median prices; descriptive, not causal):")
    if not overview.empty:
        print(overview.to_string(index=False, float_format=lambda value: f"{value:,.2f}"))

    print(
        "\nInterpretation notes:\n"
        "1. The primary trend statistic is the transaction-level quarterly median; "
        "the shaded band is the 25th–75th percentile range.\n"
        "2. Indexed charts normalize each flat type to its first quarter meeting "
        f"the n >= {MIN_QUARTERLY_N_FOR_TREND} threshold. Baseline quarters can differ.\n"
        "3. The duplicate flag marks exact matching source fields and does not prove "
        "that two records describe the same physical flat.\n"
        "4. Results describe trends and associations. They do not establish that "
        "flat type causes a price change.\n"
        "5. Prices are nominal SGD. CPI-adjusted analysis is outside this script's scope."
    )


if __name__ == "__main__":
    main()
