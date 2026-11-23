#!/usr/bin/env python3
"""Build the HDB processed v1 CSV package from five downloaded source CSVs.

Expected files in --raw-dir:
  resale.csv, property.csv, rent_micro.csv, rent_median.csv, cpi_monthly.csv

Example:
  python preprocess_hdb_v1.py --raw-dir ./raw_data --output-dir ./processed_v1
"""

from argparse import ArgumentParser
from pathlib import Path
import numpy as np
import pandas as pd


def text(s):
    return s.astype("string").str.strip().str.upper().str.replace(r"\s+", " ", regex=True)


def town(s):
    return text(s).replace({"CENTRAL": "CENTRAL AREA"})


def flat_type(s):
    x = text(s).str.replace("-", " ", regex=False).str.replace(r"\s+", " ", regex=True)
    return x.replace({
        "1 RM": "1_ROOM", "1 ROOM": "1_ROOM", "2 RM": "2_ROOM", "2 ROOM": "2_ROOM",
        "3 RM": "3_ROOM", "3 ROOM": "3_ROOM", "4 RM": "4_ROOM", "4 ROOM": "4_ROOM",
        "5 RM": "5_ROOM", "5 ROOM": "5_ROOM", "EXEC": "EXECUTIVE",
        "MULTI GENERATION": "MULTI_GENERATION",
    })


def block(s):
    return text(s).str.replace(" ", "", regex=False)


def addr(block_col, street_col):
    return block(block_col).fillna("") + "|" + text(street_col).fillna("")


def remaining_months(s):
    x = s.astype("string").str.lower()
    years = pd.to_numeric(x.str.extract(r"(\d+)\s*year")[0], errors="coerce").fillna(0)
    months = pd.to_numeric(x.str.extract(r"(\d+)\s*month")[0], errors="coerce").fillna(0)
    return (years * 12 + months).mask(x.isna()).round().astype("Int64")


def lease_band(months):
    return pd.cut(months / 12, [-np.inf, 60, 80, np.inf], right=False,
                  labels=["LT_60", "Y60_TO_79", "GE_80"]).astype("string")


def quarter(month):
    d = pd.to_datetime(month)
    return d.dt.year.astype("string") + "-Q" + d.dt.quarter.astype("string")


def save(df, output_dir, filename):
    df.to_csv(output_dir / filename, index=False, date_format="%Y-%m-%d", float_format="%.4f")


def build(raw_dir, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    required = ["resale.csv", "property.csv", "rent_micro.csv", "rent_median.csv", "cpi_monthly.csv"]
    missing = [name for name in required if not (raw_dir / name).exists()]
    if missing:
        raise FileNotFoundError("Missing input files: " + ", ".join(missing))

    resale0 = pd.read_csv(raw_dir / "resale.csv", dtype="string")
    prop0 = pd.read_csv(raw_dir / "property.csv", dtype="string")
    rent0 = pd.read_csv(raw_dir / "rent_micro.csv", dtype="string")
    median0 = pd.read_csv(raw_dir / "rent_median.csv", dtype="string")
    cpi0 = pd.read_csv(raw_dir / "cpi_monthly.csv", dtype="string")

    # Conformed Town and flat-type dimensions.
    town_names = pd.concat([town(resale0.town), town(rent0.town), town(median0.town)]).dropna().drop_duplicates().sort_values().reset_index(drop=True)
    dim_town = pd.DataFrame({"town_id": range(1, len(town_names) + 1), "town_name": town_names})
    town_id = dict(zip(dim_town.town_name, dim_town.town_id))
    type_names = pd.concat([flat_type(resale0.flat_type), flat_type(rent0.flat_type), flat_type(median0.flat_type)]).dropna().drop_duplicates().sort_values().reset_index(drop=True)
    dim_type = pd.DataFrame({"flat_type_id": range(1, len(type_names) + 1), "flat_type": type_names})
    dim_type["room_count"] = dim_type.flat_type.map({f"{n}_ROOM": n for n in range(1, 6)}).astype("Int64")
    dim_type["core_rent_analysis_flag"] = dim_type.flat_type.isin(["3_ROOM", "4_ROOM", "5_ROOM"])
    type_id = dict(zip(dim_type.flat_type, dim_type.flat_type_id))

    # Property information and union address dimension.
    prop = prop0.copy()
    prop["address_key"] = addr(prop.blk_no, prop.street)
    numeric_property = ["max_floor_lvl", "year_completed", "total_dwelling_units", "1room_sold", "2room_sold",
                        "3room_sold", "4room_sold", "5room_sold", "exec_sold", "multigen_sold",
                        "studio_apartment_sold", "1room_rental", "2room_rental", "3room_rental", "other_room_rental"]
    for col in numeric_property:
        prop[col] = pd.to_numeric(prop[col], errors="coerce").astype("Int64")
    prop = prop.drop_duplicates("address_key")
    addresses = pd.concat([
        pd.DataFrame({"block": prop.blk_no, "street_name": prop.street}),
        resale0[["block", "street_name"]], rent0[["block", "street_name"]]
    ], ignore_index=True)
    addresses["address_key"] = addr(addresses.block, addresses.street_name)
    addresses["block"] = block(addresses.block)
    addresses["street_name"] = text(addresses.street_name)
    addresses = addresses.drop_duplicates("address_key").sort_values("address_key").reset_index(drop=True)
    dim_block = addresses.merge(prop.drop(columns=["blk_no", "street"]), on="address_key", how="left")
    dim_block.insert(0, "block_id", range(1, len(dim_block) + 1))
    dim_block["property_info_available_flag"] = dim_block.year_completed.notna()
    block_id = dict(zip(dim_block.address_key, dim_block.block_id))

    # Monthly All Items CPI, 2024=100.
    cpi_source = cpi0.loc[text(cpi0.DataSeries) == "ALL ITEMS"].iloc[0]
    cpi = pd.DataFrame({"source_period": cpi_source.index[1:], "cpi_all_items_2024_base": cpi_source.iloc[1:].values})
    cpi["month_id"] = pd.to_datetime(cpi.source_period, format="%Y%b", errors="coerce")
    cpi["cpi_all_items_2024_base"] = pd.to_numeric(cpi.cpi_all_items_2024_base, errors="coerce")
    cpi = cpi.loc[cpi.month_id >= "2017-01-01", ["month_id", "cpi_all_items_2024_base"]].sort_values("month_id")

    latest = max(pd.to_datetime(resale0.month).max(), pd.to_datetime(rent0.rent_approval_date).max())
    dim_time = pd.DataFrame({"month_id": pd.date_range("2017-01-01", latest, freq="MS")})
    dim_time["year"] = dim_time.month_id.dt.year
    dim_time["month_no"] = dim_time.month_id.dt.month
    dim_time["quarter_no"] = dim_time.month_id.dt.quarter
    dim_time["quarter_id"] = dim_time.year.astype(str) + "-Q" + dim_time.quarter_no.astype(str)
    dim_time["complete_year_flag"] = dim_time.year <= 2025
    dim_time["complete_quarter_flag"] = (dim_time.year < 2026) | ((dim_time.year == 2026) & (dim_time.quarter_no <= 2))

    # Transaction-level resale fact.
    r = resale0.copy()
    r.insert(0, "resale_id", range(1, len(r) + 1))
    r["month_id"] = pd.to_datetime(r.month)
    r["quarter_id"], r["year"] = quarter(r.month_id), r.month_id.dt.year
    r["town_id"] = town(r.town).map(town_id).astype("Int64")
    r["flat_type_id"] = flat_type(r.flat_type).map(type_id).astype("Int64")
    r["address_key"] = addr(r.block, r.street_name)
    r["block_id"] = r.address_key.map(block_id).astype("Int64")
    floors = r.storey_range.str.extract(r"(\d+)\s+TO\s+(\d+)")
    r["storey_low"] = pd.to_numeric(floors[0], errors="coerce").astype("Int64")
    r["storey_high"] = pd.to_numeric(floors[1], errors="coerce").astype("Int64")
    r["storey_mid"] = (r.storey_low + r.storey_high) / 2
    r["floor_area_sqm"] = pd.to_numeric(r.floor_area_sqm, errors="coerce")
    r["lease_commence_year"] = pd.to_numeric(r.lease_commence_date, errors="coerce").astype("Int64")
    r["remaining_lease_months"] = remaining_months(r.remaining_lease)
    r["remaining_lease_group"] = lease_band(r.remaining_lease_months)
    r["resale_price_nominal_sgd"] = pd.to_numeric(r.resale_price, errors="coerce")
    r["price_per_sqm_nominal_sgd"] = r.resale_price_nominal_sgd / r.floor_area_sqm
    r["property_match_flag"] = r.address_key.isin(set(prop.address_key))
    r["potential_duplicate_flag"] = resale0.duplicated(keep=False).values
    r = r.merge(cpi, on="month_id", how="left")
    r["resale_price_real_2024_sgd"] = r.resale_price_nominal_sgd * 100 / r.cpi_all_items_2024_base
    r["price_per_sqm_real_2024_sgd"] = r.price_per_sqm_nominal_sgd * 100 / r.cpi_all_items_2024_base
    resale_cols = ["resale_id", "month_id", "quarter_id", "year", "town_id", "flat_type_id", "block_id",
                   "storey_range", "storey_low", "storey_high", "storey_mid", "floor_area_sqm", "flat_model",
                   "lease_commence_year", "remaining_lease_months", "remaining_lease_group",
                   "resale_price_nominal_sgd", "price_per_sqm_nominal_sgd", "cpi_all_items_2024_base",
                   "resale_price_real_2024_sgd", "price_per_sqm_real_2024_sgd",
                   "property_match_flag", "potential_duplicate_flag"]
    fact_resale = r[resale_cols].copy()

    # Rental-approval fact.
    x = rent0.copy()
    x.insert(0, "rental_approval_id", range(1, len(x) + 1))
    x["month_id"] = pd.to_datetime(x.rent_approval_date)
    x["quarter_id"], x["year"] = quarter(x.month_id), x.month_id.dt.year
    x["town_id"] = town(x.town).map(town_id).astype("Int64")
    x["flat_type_id"] = flat_type(x.flat_type).map(type_id).astype("Int64")
    x["address_key"] = addr(x.block, x.street_name)
    x["block_id"] = x.address_key.map(block_id).astype("Int64")
    x["monthly_rent_nominal_sgd"] = pd.to_numeric(x.monthly_rent, errors="coerce")
    x["property_match_flag"] = x.address_key.isin(set(prop.address_key))
    x["potential_duplicate_flag"] = rent0.duplicated(keep=False).values
    x = x.merge(cpi, on="month_id", how="left")
    x["monthly_rent_real_2024_sgd"] = x.monthly_rent_nominal_sgd * 100 / x.cpi_all_items_2024_base
    rent_cols = ["rental_approval_id", "month_id", "quarter_id", "year", "town_id", "flat_type_id", "block_id",
                 "monthly_rent_nominal_sgd", "cpi_all_items_2024_base", "monthly_rent_real_2024_sgd",
                 "property_match_flag", "potential_duplicate_flag"]
    fact_rent = x[rent_cols].copy()

    # Official quarterly median rent.
    m = median0.copy()
    m["town_id"] = town(m.town).map(town_id).astype("Int64")
    m["flat_type_id"] = flat_type(m.flat_type).map(type_id).astype("Int64")
    m["median_rent_nominal_sgd"] = pd.to_numeric(m.median_rent, errors="coerce")
    fact_median = m.groupby(["quarter", "town_id", "flat_type_id"], dropna=False).agg(
        median_rent_nominal_sgd=("median_rent_nominal_sgd", "median"),
        source_rows_aggregated=("median_rent", "size")
    ).reset_index().rename(columns={"quarter": "quarter_id"})
    fact_median.insert(0, "median_rent_id", range(1, len(fact_median) + 1))
    fact_median["median_rent_available_flag"] = fact_median.median_rent_nominal_sgd.notna()

    # Town/type/quarter mart.
    rq = fact_resale.groupby(["quarter_id", "town_id", "flat_type_id"], as_index=False).agg(
        resale_n=("resale_id", "size"),
        median_resale_price_nominal_sgd=("resale_price_nominal_sgd", "median"),
        median_price_per_sqm_nominal_sgd=("price_per_sqm_nominal_sgd", "median"),
        median_resale_price_real_2024_sgd=("resale_price_real_2024_sgd", "median"),
        median_price_per_sqm_real_2024_sgd=("price_per_sqm_real_2024_sgd", "median"),
        median_remaining_lease_months=("remaining_lease_months", "median"))
    cq = cpi.assign(quarter_id=quarter(cpi.month_id)).groupby("quarter_id", as_index=False).agg(
        quarter_cpi_all_items_2024_base=("cpi_all_items_2024_base", "mean"))
    mart_q = rq.merge(fact_median[["quarter_id", "town_id", "flat_type_id", "median_rent_nominal_sgd",
                                  "median_rent_available_flag"]], on=["quarter_id", "town_id", "flat_type_id"], how="left").merge(cq, on="quarter_id", how="left")
    mart_q["median_rent_real_2024_sgd"] = mart_q.median_rent_nominal_sgd * 100 / mart_q.quarter_cpi_all_items_2024_base
    mart_q["estimated_gross_yield_pct"] = mart_q.median_rent_nominal_sgd * 12 / mart_q.median_resale_price_nominal_sgd * 100
    core_ids = set(dim_type.loc[dim_type.core_rent_analysis_flag, "flat_type_id"])
    mart_q["rent_core_scope_flag"] = mart_q.flat_type_id.isin(core_ids)
    mart_q["yield_usable_flag"] = mart_q.median_rent_nominal_sgd.notna() & mart_q.rent_core_scope_flag
    mart_q["complete_quarter_flag"] = mart_q.quarter_id <= "2026-Q2"
    mart_q.insert(0, "town_flat_quarter_id", range(1, len(mart_q) + 1))

    # Same-block/type/year yield mart.
    ry = fact_rent.groupby(["year", "block_id", "town_id", "flat_type_id"], as_index=False).agg(
        rental_n=("rental_approval_id", "size"),
        median_monthly_rent_nominal_sgd=("monthly_rent_nominal_sgd", "median"),
        median_monthly_rent_real_2024_sgd=("monthly_rent_real_2024_sgd", "median"))
    sy = fact_resale.loc[fact_resale.year >= 2021].groupby(["year", "block_id", "town_id", "flat_type_id"], as_index=False).agg(
        resale_n=("resale_id", "size"),
        median_resale_price_nominal_sgd=("resale_price_nominal_sgd", "median"),
        median_resale_price_real_2024_sgd=("resale_price_real_2024_sgd", "median"),
        median_remaining_lease_months=("remaining_lease_months", "median"))
    mart_y = ry.merge(sy, on=["year", "block_id", "town_id", "flat_type_id"], how="inner")
    mart_y["estimated_gross_yield_pct"] = mart_y.median_monthly_rent_nominal_sgd * 12 / mart_y.median_resale_price_nominal_sgd * 100
    mart_y["remaining_lease_group"] = lease_band(mart_y.median_remaining_lease_months)
    mart_y["low_sample_flag"] = (mart_y.rental_n < 3) | (mart_y.resale_n < 3)
    mart_y["complete_year_flag"] = mart_y.year <= 2025
    mart_y["analysis_eligible_flag"] = ~mart_y.low_sample_flag & mart_y.complete_year_flag
    mart_y.insert(0, "block_flat_year_id", range(1, len(mart_y) + 1))

    # Validation: stop instead of silently producing structurally invalid outputs.
    assert dim_town.town_id.is_unique and dim_type.flat_type_id.is_unique and dim_block.block_id.is_unique
    assert fact_resale.resale_id.is_unique and fact_rent.rental_approval_id.is_unique
    assert not fact_resale[["town_id", "flat_type_id", "block_id"]].isna().any().any()
    assert not fact_rent[["town_id", "flat_type_id", "block_id"]].isna().any().any()
    assert not mart_q.duplicated(["quarter_id", "town_id", "flat_type_id"]).any()
    assert not mart_y.duplicated(["year", "block_id", "town_id", "flat_type_id"]).any()

    for frame, filename in [
        (dim_time, "dim_time_month.csv"), (dim_town, "dim_town.csv"),
        (dim_type, "dim_flat_type.csv"), (dim_block, "dim_block.csv"), (cpi, "dim_cpi_month.csv"),
        (fact_resale, "fact_resale_transaction.csv"), (fact_rent, "fact_rental_approval.csv"),
        (fact_median, "fact_quarterly_median_rent.csv"),
        (mart_q, "mart_town_flat_quarter.csv"), (mart_y, "mart_block_flat_year_yield.csv")]:
        save(frame, output_dir, filename)

    print(f"Completed: {output_dir.resolve()}")
    print(f"resale={len(fact_resale):,}; rent={len(fact_rent):,}; quarter_mart={len(mart_q):,}; block_year_mart={len(mart_y):,}")


if __name__ == "__main__":
    parser = ArgumentParser(description="Create the HDB processed v1 analysis package")
    parser.add_argument("--raw-dir", type=Path, required=True, help="Directory containing the five source CSV files")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory for processed CSV outputs")
    args = parser.parse_args()
    build(args.raw_dir, args.output_dir)
