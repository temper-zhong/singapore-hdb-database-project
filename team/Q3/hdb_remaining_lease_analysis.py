"""RQ3: remaining lease, resale price and estimated gross yield.

The script queries the shared MySQL database directly and does not read CSV
files. It reproduces the tables and figures in the output folder.

Install dependencies:
    python -m pip install PyMySQL pandas numpy Pillow

Run:
    python hdb_remaining_lease_analysis.py
"""

from __future__ import annotations

import getpass
import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pymysql
from PIL import Image, ImageDraw, ImageFont


GROUP_ORDER = ["GE_80", "Y60_TO_79", "LT_60"]
GROUP_LABELS = {"GE_80": ">=80 years", "Y60_TO_79": "60-79 years", "LT_60": "<60 years"}
COLORS = {"GE_80": "#4C78A8", "Y60_TO_79": "#F2A541", "LT_60": "#D1495B"}

DB_CONFIG = {
    "host": os.environ.get("HDB_DB_HOST", "localhost"),
    "port": int(os.environ.get("HDB_DB_PORT", "3306")),
    "user": os.environ.get("HDB_DB_USER", "root"),
    "database": os.environ.get("HDB_DB_NAME", "hdb_project"),
}

OUTPUT_DIR = Path(__file__).resolve().parent / "hdb_remaining_lease_analysis_output"


PRICE_SQL = """
SELECT
    r.resale_id, r.block_id, r.year, r.quarter_id, t.town_name, f.flat_type,
    r.flat_model, r.floor_area_sqm, r.storey_mid, r.remaining_lease_months,
    r.remaining_lease_group, r.resale_price_nominal_sgd,
    r.resale_price_real_2024_sgd, r.price_per_sqm_real_2024_sgd,
    r.potential_duplicate_flag
FROM fact_resale_transaction AS r
JOIN dim_town AS t ON t.town_id = r.town_id
JOIN dim_flat_type AS f ON f.flat_type_id = r.flat_type_id
WHERE r.year BETWEEN 2021 AND 2025
  AND r.resale_price_real_2024_sgd IS NOT NULL
  AND r.floor_area_sqm > 0
  AND r.storey_mid IS NOT NULL
"""


YIELD_SQL = """
SELECT
    y.block_flat_year_id, y.block_id, y.year, t.town_name, f.flat_type,
    f.core_rent_analysis_flag, y.rental_n, y.resale_n,
    y.median_remaining_lease_months, y.remaining_lease_group,
    y.median_monthly_rent_real_2024_sgd,
    y.median_resale_price_real_2024_sgd, y.estimated_gross_yield_pct
FROM mart_block_flat_year_yield AS y
JOIN dim_town AS t ON t.town_id = y.town_id
JOIN dim_flat_type AS f ON f.flat_type_id = y.flat_type_id
WHERE y.analysis_eligible_flag = 1
  AND y.year BETWEEN 2021 AND 2025
  AND y.estimated_gross_yield_pct IS NOT NULL
"""


def normal_p_value(z: float) -> float:
    return max(math.erfc(abs(z) / math.sqrt(2.0)), np.finfo(float).tiny)


def make_design(
    frame: pd.DataFrame,
    outcome: str,
    categorical: list[str],
    numeric: list[str],
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    pieces = []
    names = ["Intercept"]
    for col in categorical:
        if col == "remaining_lease_group":
            values = pd.Categorical(frame[col], categories=GROUP_ORDER, ordered=True)
        else:
            values = pd.Categorical(frame[col])
        dummies = pd.get_dummies(values, prefix=col, drop_first=True, dtype=float)
        pieces.append(dummies.reset_index(drop=True))
        names.extend(dummies.columns.tolist())
    for col in numeric:
        values = frame[col].astype(float)
        sd = values.std(ddof=0)
        z = (values - values.mean()) / sd if sd > 0 else values * 0
        pieces.append(pd.DataFrame({f"z_{col}": z.to_numpy()}))
        names.append(f"z_{col}")
    body = pd.concat(pieces, axis=1).to_numpy(dtype=float)
    X = np.column_stack([np.ones(len(frame)), body])
    y = frame[outcome].to_numpy(dtype=float)
    return X, y, names


def cluster_ols(
    frame: pd.DataFrame,
    outcome: str,
    categorical: list[str],
    numeric: list[str],
    cluster: str,
) -> pd.DataFrame:
    X, y, names = make_design(frame.reset_index(drop=True), outcome, categorical, numeric)
    beta, _, rank, _ = np.linalg.lstsq(X, y, rcond=None)
    if rank < X.shape[1]:
        raise RuntimeError(f"Design matrix is rank deficient: rank={rank}, columns={X.shape[1]}")
    resid = y - X @ beta
    bread = np.linalg.pinv(X.T @ X)
    clusters = frame[cluster].astype(str).to_numpy()
    meat = np.zeros((X.shape[1], X.shape[1]), dtype=float)
    unique_clusters = np.unique(clusters)
    for value in unique_clusters:
        idx = clusters == value
        score = X[idx].T @ resid[idx]
        meat += np.outer(score, score)
    n, p = X.shape
    g = len(unique_clusters)
    correction = (g / (g - 1)) * ((n - 1) / (n - p)) if g > 1 and n > p else 1.0
    covariance = correction * bread @ meat @ bread
    se = np.sqrt(np.maximum(np.diag(covariance), 0))
    z = np.divide(beta, se, out=np.full_like(beta, np.nan), where=se > 0)
    result = pd.DataFrame(
        {
            "term": names,
            "estimate": beta,
            "cluster_se": se,
            "z": z,
            "p_value_normal_approx": [normal_p_value(v) if np.isfinite(v) else np.nan for v in z],
            "ci_low": beta - 1.96 * se,
            "ci_high": beta + 1.96 * se,
            "n": n,
            "clusters": g,
        }
    )
    return result


def describe(frame: pd.DataFrame, value: str) -> pd.DataFrame:
    out = (
        frame.groupby("remaining_lease_group", observed=True)[value]
        .agg(n="size", mean="mean", median="median", q25=lambda x: x.quantile(0.25), q75=lambda x: x.quantile(0.75))
        .reindex(GROUP_ORDER)
        .reset_index()
    )
    return out


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "arialbd.ttf" if bold else "arial.ttf"
    windows_dir = os.environ.get("WINDIR")
    if windows_dir:
        candidate = Path(windows_dir) / "Fonts" / name
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default(size=size)


def draw_axis(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], lo: float, hi: float, ticks: int, fmt) -> None:
    x0, y0, x1, y1 = box
    draw.line((x0, y1, x1, y1), fill="#7A8594", width=2)
    for i in range(ticks + 1):
        value = lo + (hi - lo) * i / ticks
        x = x0 + (x1 - x0) * i / ticks
        draw.line((x, y1, x, y1 + 8), fill="#7A8594", width=2)
        label = fmt(value)
        bbox = draw.textbbox((0, 0), label, font=font(22))
        draw.text((x - (bbox[2] - bbox[0]) / 2, y1 + 12), label, fill="#4A5563", font=font(22))


def descriptive_chart(price_desc: pd.DataFrame, yield_desc: pd.DataFrame, output: Path) -> None:
    img = Image.new("RGB", (1800, 1000), "white")
    draw = ImageDraw.Draw(img)
    draw.text((80, 55), "Remaining lease and HDB market outcomes, 2021-2025", fill="#17202A", font=font(42, True))
    draw.text((80, 112), "Raw medians; resale prices are expressed in 2024 SGD", fill="#566573", font=font(26))

    panels = [
        (price_desc, "Median resale price (2024 SGD)", 0, lambda v: f"${v/1000:.0f}k"),
        (yield_desc, "Median estimated gross yield", 900, lambda v: f"{v:.1f}%"),
    ]
    for data, title, offset, tickfmt in panels:
        draw.text((80 + offset, 200), title, fill="#17202A", font=font(30, True))
        left, top, right, bottom = 310 + offset, 290, 820 + offset, 780
        lo = 0.0
        hi = float(data["q75"].max()) * 1.18
        draw_axis(draw, (left, top, right, bottom), lo, hi, 5, tickfmt)
        for i, row in data.iterrows():
            y = top + 90 + i * 145
            group = row["remaining_lease_group"]
            draw.text((85 + offset, y - 18), GROUP_LABELS[group], fill="#273746", font=font(26, True))
            xq1 = left + (right - left) * (row["q25"] - lo) / (hi - lo)
            xmed = left + (right - left) * (row["median"] - lo) / (hi - lo)
            xq3 = left + (right - left) * (row["q75"] - lo) / (hi - lo)
            draw.line((xq1, y, xq3, y), fill=COLORS[group], width=18)
            draw.ellipse((xmed - 12, y - 12, xmed + 12, y + 12), fill="#17202A")
            draw.text((right - 5, y - 55), f"n={int(row['n']):,}", anchor="ra", fill="#6B7785", font=font(20))
        draw.text((left, 855), "Bar: interquartile range    Dot: median", fill="#566573", font=font(22))

    img.save(output, dpi=(180, 180))


def adjusted_chart(price_effects: pd.DataFrame, yield_effects: pd.DataFrame, output: Path) -> None:
    img = Image.new("RGB", (1800, 1000), "white")
    draw = ImageDraw.Draw(img)
    draw.text((80, 55), "Adjusted differences relative to flats with >=80 years remaining", fill="#17202A", font=font(40, True))
    draw.text((80, 112), "Dots are model estimates; lines are cluster-robust 95% confidence intervals", fill="#566573", font=font(26))

    panels = [
        (price_effects, "Resale price difference", "%", 0, "Controls: town, type/model, area, storey, quarter"),
        (yield_effects, "Gross-yield difference", "percentage points", 900, "Controls: town, flat type, year"),
    ]
    for data, title, unit, offset, controls in panels:
        draw.text((80 + offset, 205), title, fill="#17202A", font=font(30, True))
        minimum = min(float(data["ci_low_display"].min()), 0.0)
        maximum = max(float(data["ci_high_display"].max()), 0.0)
        pad = max((maximum - minimum) * 0.25, 0.5)
        lo, hi = minimum - pad, maximum + pad
        left, top, right, bottom = 310 + offset, 320, 820 + offset, 730
        draw_axis(draw, (left, top, right, bottom), lo, hi, 5, lambda v: f"{v:.1f}")
        xzero = left + (right - left) * (0 - lo) / (hi - lo)
        draw.line((xzero, top - 35, xzero, bottom), fill="#AAB2BD", width=3)
        for i, row in data.reset_index(drop=True).iterrows():
            y = top + 95 + i * 160
            group = row["group"]
            draw.text((85 + offset, y - 18), GROUP_LABELS[group], fill="#273746", font=font(26, True))
            xlo = left + (right - left) * (row["ci_low_display"] - lo) / (hi - lo)
            xhi = left + (right - left) * (row["ci_high_display"] - lo) / (hi - lo)
            xest = left + (right - left) * (row["estimate_display"] - lo) / (hi - lo)
            draw.line((xlo, y, xhi, y), fill=COLORS[group], width=8)
            draw.ellipse((xest - 13, y - 13, xest + 13, y + 13), fill=COLORS[group], outline="#17202A", width=2)
            sign = "+" if row["estimate_display"] >= 0 else ""
            draw.text((right, y - 55), f"{sign}{row['estimate_display']:.1f} {unit}", anchor="ra", fill="#17202A", font=font(22, True))
        draw.text((left, 820), controls, fill="#566573", font=font(21))
    img.save(output, dpi=(180, 180))


def extract_group_effects(model: pd.DataFrame, price: bool) -> pd.DataFrame:
    lookup = {
        "remaining_lease_group_Y60_TO_79": "Y60_TO_79",
        "remaining_lease_group_LT_60": "LT_60",
    }
    out = model[model["term"].isin(lookup)].copy()
    out["group"] = out["term"].map(lookup)
    if price:
        for source, dest in [("estimate", "estimate_display"), ("ci_low", "ci_low_display"), ("ci_high", "ci_high_display")]:
            out[dest] = (np.exp(out[source]) - 1) * 100
    else:
        out["estimate_display"] = out["estimate"]
        out["ci_low_display"] = out["ci_low"]
        out["ci_high_display"] = out["ci_high"]
    return out


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    password = os.environ.get("HDB_DB_PASSWORD") or getpass.getpass("MySQL root password: ")
    conn = pymysql.connect(password=password, charset="utf8mb4", **DB_CONFIG)
    try:
        price = pd.read_sql_query(PRICE_SQL, conn)
        yields_all = pd.read_sql_query(YIELD_SQL, conn)
    finally:
        conn.close()

    price["remaining_lease_group"] = pd.Categorical(price["remaining_lease_group"], categories=GROUP_ORDER, ordered=True)
    yields_all["remaining_lease_group"] = pd.Categorical(yields_all["remaining_lease_group"], categories=GROUP_ORDER, ordered=True)
    yields_core = yields_all[yields_all["core_rent_analysis_flag"] == 1].copy()
    price["log_real_resale_price"] = np.log(price["resale_price_real_2024_sgd"])
    price["log_floor_area_sqm"] = np.log(price["floor_area_sqm"])
    # Flat model is largely nested within flat type. A joint category controls both
    # without introducing exact collinearity between two overlapping dummy sets.
    price["flat_type_model"] = price["flat_type"].astype(str) + " / " + price["flat_model"].astype(str)

    price_desc = describe(price, "resale_price_real_2024_sgd")
    ppsm_desc = describe(price, "price_per_sqm_real_2024_sgd")
    yield_desc = describe(yields_core, "estimated_gross_yield_pct")
    yield_all_desc = describe(yields_all, "estimated_gross_yield_pct")

    price_model = cluster_ols(
        price,
        "log_real_resale_price",
        ["remaining_lease_group", "town_name", "flat_type_model", "quarter_id"],
        ["log_floor_area_sqm", "storey_mid"],
        "block_id",
    )
    price_no_dupes = price[price["potential_duplicate_flag"] == 0].copy()
    price_model_no_dupes = cluster_ols(
        price_no_dupes,
        "log_real_resale_price",
        ["remaining_lease_group", "town_name", "flat_type_model", "quarter_id"],
        ["log_floor_area_sqm", "storey_mid"],
        "block_id",
    )
    yield_model = cluster_ols(
        yields_core,
        "estimated_gross_yield_pct",
        ["remaining_lease_group", "town_name", "flat_type", "year"],
        [],
        "block_id",
    )
    yield_model_all = cluster_ols(
        yields_all,
        "estimated_gross_yield_pct",
        ["remaining_lease_group", "town_name", "flat_type", "year"],
        [],
        "block_id",
    )

    tables = {
        "price_descriptive.csv": price_desc,
        "price_per_sqm_descriptive.csv": ppsm_desc,
        "yield_descriptive_core_345.csv": yield_desc,
        "yield_descriptive_all_types.csv": yield_all_desc,
        "price_model_clustered.csv": price_model,
        "price_model_excluding_duplicates.csv": price_model_no_dupes,
        "yield_model_core_345_clustered.csv": yield_model,
        "yield_model_all_types_clustered.csv": yield_model_all,
    }
    for name, frame in tables.items():
        frame.to_csv(OUTPUT_DIR / name, index=False, encoding="utf-8-sig")

    price_effects = extract_group_effects(price_model, price=True)
    yield_effects = extract_group_effects(yield_model, price=False)
    price_effects.to_csv(OUTPUT_DIR / "price_adjusted_group_effects.csv", index=False, encoding="utf-8-sig")
    yield_effects.to_csv(OUTPUT_DIR / "yield_adjusted_group_effects.csv", index=False, encoding="utf-8-sig")

    descriptive_chart(price_desc, yield_desc, OUTPUT_DIR / "rq3_descriptive.png")
    adjusted_chart(price_effects, yield_effects, OUTPUT_DIR / "rq3_adjusted_effects.png")

    summary = {
        "database": DB_CONFIG["database"],
        "database_server": "MySQL/MariaDB",
        "price_rows": len(price),
        "price_blocks": int(price["block_id"].nunique()),
        "price_potential_duplicates": int(price["potential_duplicate_flag"].sum()),
        "yield_rows_all_eligible": len(yields_all),
        "yield_rows_core_345": len(yields_core),
        "yield_blocks_core_345": int(yields_core["block_id"].nunique()),
        "price_effects_pct_vs_ge80": price_effects[["group", "estimate_display", "ci_low_display", "ci_high_display", "p_value_normal_approx"]].to_dict("records"),
        "yield_effects_pp_vs_ge80": yield_effects[["group", "estimate_display", "ci_low_display", "ci_high_display", "p_value_normal_approx"]].to_dict("records"),
    }
    (OUTPUT_DIR / "analysis_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
