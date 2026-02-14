"""
Picking Time Simulation
=============================================

Pipeline:
1) Load pick data from Excel
2) Merge layout coordinates from df_part2
3) Simulate travel + pick time using custom rules
4) Output per-pick and per-picker timing metrics

Travel Time Rules:
- Horizontal (x): 1.0 time unit per unit distance
- Vertical (y): 0.5 time units if y changes at all
- Floor OR pick module change: +5.0 time units

Pick Time Rule:
- 3.0 fixed time units per pick
- +0.4 time units per unit picked
"""

from __future__ import annotations
import pandas as pd
import sys
from pathlib import Path

# ---------------------------------------------------------
# 0) Import df_part2 from mod_pick.py
# ---------------------------------------------------------
# Add ../picking_area_layouts to Python path
BASE_DIR = Path(__file__).resolve().parent
LAYOUT_DIR = (BASE_DIR / ".." / "picking_area_layouts").resolve()
sys.path.insert(0, str(LAYOUT_DIR))

# Import layout module
import mod_pick  # noqa: E402

# EXPECTED: df_part2 exists in mod_pick.py
df_part1 = mod_pick.df_part1

# ---------------------------------------------------------
# 1) Load Picks Sheet from Excel
# ---------------------------------------------------------
def load_picks_excel(path: str) -> pd.DataFrame:
    """
    Loads the picks sheet and normalizes key fields.
    """

    df = pd.read_excel(
        path,
        sheet_name="Picks (Mod) - Week of 1-12-26",
        dtype=str
    ).fillna("")

    # Clean column names
    df.columns = [c.strip() for c in df.columns]

    # Parse timestamp
    df["Created On"] = pd.to_datetime(df["Created On"], errors="raise")

    # Quantity processed
    df["Quantity Processed"] = (
        pd.to_numeric(df["Quantity Processed"], errors="raise")
        .astype(int)
    )

    # Picker identifier
    if "User Name" in df.columns and (df["User Name"] != "").any():
        df["picker_id"] = df["User Name"].astype(str)
    else:
        df["picker_id"] = df["Full Name"].astype(str)

    # Normalize location join key
    df["location_id"] = (
        df["From Location (Pick Module)"]
        .astype(str)
        .str.strip()
    )

    # Sort into walking order
    df = df.sort_values(
        ["picker_id", "Created On"]
    ).reset_index(drop=True)

    return df


# ---------------------------------------------------------
# 2) Prepare layout / heatmap data (df_part2)
# ---------------------------------------------------------
def prepare_layout_df(df_part2: pd.DataFrame) -> pd.DataFrame:
    """
    Normalizes layout dataframe and keeps only required columns.
    """

    df = df_part2.copy()

    # Normalize column names
    df.columns = [c.strip() for c in df.columns]

    required = {
        "location_id", "pick_module", "floor", "x", "y"
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"df_part2 missing columns: {sorted(missing)}")

    # Clean join key
    df["location_id"] = df["location_id"].astype(str).str.strip()

    # Ensure numeric types
    for c in ["x", "y", "floor"]:
        df[c] = pd.to_numeric(df[c], errors="raise").astype(int)

    df["pick_module"] = df["pick_module"].astype(str)

    # Deduplicate (one row per location)
    df = df.drop_duplicates(subset=["location_id"])

    return df[["location_id", "pick_module", "floor", "x", "y"]]


# ---------------------------------------------------------
# 3) Travel + Pick Time Models
# ---------------------------------------------------------
def travel_time_units(prev_row: pd.Series, row: pd.Series) -> float:
    """
    Travel time between two consecutive picks.
    """

    # Horizontal movement
    dx = abs(row["x"] - prev_row["x"])
    horizontal = dx * 1.0

    # Vertical movement (binary)
    vertical = 0.5 if row["y"] != prev_row["y"] else 0.0

    # Floor OR module change penalty
    cross_penalty = (
        5.0
        if (row["floor"] != prev_row["floor"]) or
           (row["pick_module"] != prev_row["pick_module"])
        else 0.0
    )

    return horizontal + vertical + cross_penalty


def pick_time_units(qty: int) -> float:
    """
    Pick handling time.
    """
    return 3.0 + 0.4 * qty


# ---------------------------------------------------------
# 4) Simulation
# ---------------------------------------------------------
def simulate_picking_times(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds:
    - travel_time
    - pick_time
    - total_time
    - cumulative_time_by_picker
    """

    df = df.copy()

    df["pick_time"] = df["Quantity Processed"].apply(pick_time_units)
    df["travel_time"] = 0.0

    for picker_id, idxs in df.groupby("picker_id").groups.items():
        idxs = list(idxs)
        for i in range(1, len(idxs)):
            df.at[idxs[i], "travel_time"] = travel_time_units(
                df.loc[idxs[i - 1]],
                df.loc[idxs[i]],
            )

    df["total_time"] = df["pick_time"] + df["travel_time"]
    df["cumulative_time_by_picker"] = (
        df.groupby("picker_id")["total_time"].cumsum()
    )

    return df


# ---------------------------------------------------------
# 5) Run Everything
# ---------------------------------------------------------
if __name__ == "__main__":

    # ---- INPUTS ----
    EXCEL_PATH = "../data/Operations_Data_Week_of_1-12-26.xlsx"

    # ---- LOAD DATA ----
    df_picks = load_picks_excel(EXCEL_PATH)
    df_layout = prepare_layout_df(df_part1)

    # ---- MERGE COORDINATES ----
    df_merged = df_picks.merge(
        df_layout,
        on="location_id",
        how="left",
        validate="many_to_one"
    )

    # Sanity check
    missing = df_merged[df_merged["x"].isna()]["location_id"].unique()
    if len(missing) > 0:
        print("WARNING: Missing layout mapping for locations:")
        print(missing[:10])

    # ---- SIMULATE ----
    df_sim = simulate_picking_times(df_merged)

    # ---- OUTPUT EXAMPLES ----
    print("\nSample simulated picks:")
    print(df_sim[[
        "picker_id",
        "Created On",
        "location_id",
        "pick_module",
        "floor",
        "x", "y",
        "Quantity Processed",
        "travel_time",
        "pick_time",
        "total_time",
        "cumulative_time_by_picker",
    ]].head(20))

    totals = (
        df_sim.groupby("picker_id", as_index=False)["total_time"]
        .sum()
        .assign(total_minutes=lambda d: d["total_time"] / 60)
    )

    print("\nTotal minutes per picker:")
    print(totals.sort_values("total_minutes", ascending=False))
