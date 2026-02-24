"""
Spatial Picking Simulation + Capacity Analysis
==============================================

PIPELINE
--------
1) Import slotting layout
2) Load pick transactions from Excel
3) Merge spatial coordinates onto picks
4) Simulate travel + picker service time
5) Compute hourly capacity & utilization
6) Visualize spatial movement

TRAVEL MODEL
------------
- Horizontal: 1.0 per X distance
- Vertical aisle change: +0.5 if Y changes
- Module OR floor change: +5.0
- Missing layout rows → travel = 0

PICKER SERVICE MODEL
--------------------
Per stop:
- scan tote
- scan location
- grab items
- drop into tote
"""

from __future__ import annotations

import sys
import glob
import os
import math
import itertools
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import pygame
import importlib

# =========================================================
# SLOT STRATEGIES
# =========================================================

SLOT_STRATEGIES = [
    {
        "name": "Original_Slotting",
        "type": "module",
        "module": "mod_pick",
    },
    {
        "name": "Slotting_Scenario_1",
        "type": "csv",
        "path": "../data/Heat Map - Pick Modules - Scenario 1.csv",
    },
]

# =========================================================
# AUTO-BUILD PICK SCENARIOS (Excel + CSV supported)
# =========================================================

HEATMAP_MODULE = "mod_pick"

# ---------------------------------------------------------
# Find all pick data files (both Excel and CSV)
# ---------------------------------------------------------
pick_excel_files = sorted(glob.glob("../data/Operations_Data_*.xlsx"))
pick_csv_files   = sorted(glob.glob("../data/Staffing_*.csv"))

pick_files = pick_excel_files + pick_csv_files

SCENARIOS = []

for slot in SLOT_STRATEGIES:
    for path in pick_files:
        name = os.path.splitext(os.path.basename(path))[0]

        # Try to extract worker count from filename if present
        # e.g., Operations_Data_24_Workers.csv
        workers = None
        parts = name.split("_")
        for p in parts:
            if p.isdigit():
                workers = int(p)

        # fallback if not in filename
        if workers is None:
            workers = 24  # <-- safe default (change if needed)

        SCENARIOS.append({
            "name": name,
            "slot_strategy": slot,
            "picks_path": path,
            "employees": workers,
        })

print(f"Loaded {len(SCENARIOS)} pick scenarios")


# =========================================================
# IMPORT LAYOUT
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
LAYOUT_DIR = (BASE_DIR / ".." / "picking_area_layouts").resolve()
sys.path.insert(0, str(LAYOUT_DIR))

import mod_pick  # noqa

df_part1 = mod_pick.df_part1


# =========================================================
# LOAD PICKS
# =========================================================

def load_picks_excel(path: str) -> pd.DataFrame:
    df = pd.read_excel(
        path,
        sheet_name="Picks (Mod) - Week of 1-12-26",
        dtype=str,
    ).fillna("")

    df.columns = [c.strip() for c in df.columns]

    df["Created On"] = pd.to_datetime(df["Created On"], errors="raise")
    df["Quantity Processed"] = pd.to_numeric(
        df["Quantity Processed"], errors="raise"
    ).astype(int)

    if "User Name" in df.columns and (df["User Name"] != "").any():
        df["picker_id"] = df["User Name"]
    else:
        df["picker_id"] = df["Full Name"]

    df["location_id"] = df["From Location (Pick Module)"].astype(str).str.strip()

    df = df.sort_values(["picker_id", "Created On"]).reset_index(drop=True)
    return df

# =========================================================
# LOAD LAYOUT FROM CSV
# =========================================================

def load_layout_from_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)

    df.columns = [c.strip() for c in df.columns]

    required = {"location_id", "pick_module", "floor", "x", "y"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Layout CSV missing columns: {missing}")

    df["location_id"] = df["location_id"].astype(str).str.strip()

    for c in ["x", "y", "floor"]:
        df[c] = pd.to_numeric(df[c], errors="raise")

    df["pick_module"] = df["pick_module"].astype(str)

    return df[["location_id", "pick_module", "floor", "x", "y"]]

# =========================================================
# UNIVERSAL PICKS LOADER (CSV or XLSX)
# =========================================================
def load_picks_universal(path: str) -> pd.DataFrame:
    """
    Loads picks from either:
    - Excel (Operations format)
    - CSV (same schema)

    Returns standardized dataframe.
    """

    if path.lower().endswith(".xlsx"):
        return load_picks_excel(path)

    elif path.lower().endswith(".csv"):
        df = pd.read_csv(path, dtype=str).fillna("")
        df.columns = [c.strip() for c in df.columns]

        df["Created On"] = pd.to_datetime(df["Created On"], errors="raise")
        df["Quantity Processed"] = (
            pd.to_numeric(df["Quantity Processed"], errors="raise")
            .astype(int)
        )

        if "User Name" in df.columns and (df["User Name"] != "").any():
            df["picker_id"] = df["User Name"]
        else:
            df["picker_id"] = df["Full Name"]

        df["location_id"] = (
            df["From Location (Pick Module)"]
            .astype(str)
            .str.strip()
        )

        df = df.sort_values(["picker_id", "Created On"]).reset_index(drop=True)
        return df

    else:
        raise ValueError(f"Unsupported file type: {path}")

# =========================================================
# UNIVERSAL LAYOUT LOADER (CSV or XLSX)
# =========================================================
def load_layout_universal(path: str, file_type: str) -> pd.DataFrame:
    """
    Loads layout from either CSV or XLSX and standardizes columns.
    """

    if file_type == "csv":
        df = pd.read_csv(path)

    elif file_type == "xlsx":
        df = pd.read_excel(path)

    else:
        raise ValueError(f"Unsupported layout type: {file_type}")

    df.columns = [c.strip() for c in df.columns]

    required = {"location_id", "pick_module", "floor", "x", "y"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Layout file missing columns: {missing}")

    # normalize types
    df["location_id"] = df["location_id"].astype(str).str.strip()
    df["pick_module"] = df["pick_module"].astype(str)

    for c in ["x", "y", "floor"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df[["location_id", "pick_module", "floor", "x", "y"]]

# =========================================================
# SMART LAYOUT LOADER (handles module vs csv/xlsx)
# =========================================================
def load_layout_for_strategy(slot_strategy: dict) -> pd.DataFrame:
    """
    Returns standardized layout dataframe regardless of source.

    Supports:
      - type = "module"  → uses mod_pick.df_part1
      - type = "csv"
      - type = "xlsx"
    """

    strategy_type = slot_strategy["type"]

    # -----------------------------------------------------
    # CASE 1 — Original baseline via mod_pick
    # -----------------------------------------------------
    if strategy_type == "module":
        module_name = slot_strategy["module"]
        layout_module = importlib.import_module(module_name)

        df_layout = prepare_layout_df(layout_module.df_part1.copy())
        return df_layout

    # -----------------------------------------------------
    # CASE 2 — Direct file load
    # -----------------------------------------------------
    elif strategy_type in ("csv", "xlsx"):
        return load_layout_universal(
            slot_strategy["path"],
            strategy_type,
        )

    else:
        raise ValueError(f"Unknown slot strategy type: {strategy_type}")

# =========================================================
# PREPARE LAYOUT
# =========================================================

def prepare_layout_df(df_part1: pd.DataFrame) -> pd.DataFrame:
    df = df_part1.copy()
    df.columns = [c.strip() for c in df.columns]

    required = {"location_id", "pick_module", "floor", "x", "y"}
    if not required.issubset(df.columns):
        raise ValueError("df_part1 missing required columns")

    df["location_id"] = df["location_id"].astype(str).str.strip()

    for c in ["x", "y", "floor"]:
        df[c] = pd.to_numeric(df[c], errors="raise").astype(int)

    df["pick_module"] = df["pick_module"].astype(str)

    df = df.drop_duplicates(subset=["location_id"])

    return df[["location_id", "pick_module", "floor", "x", "y"]]


# =========================================================
# TIME MODELS
# =========================================================

def travel_time_units(prev_row: pd.Series, row: pd.Series) -> float:
    required = ["x", "y", "floor", "pick_module"]
    if any(pd.isna(prev_row[c]) or pd.isna(row[c]) for c in required):
        return 0.0

    horizontal = abs(row["x"] - prev_row["x"])
    vertical = 0.5 if row["y"] != prev_row["y"] else 0.0
    cross = (
        5.0
        if (row["floor"] != prev_row["floor"])
        or (row["pick_module"] != prev_row["pick_module"])
        else 0.0
    )

    return horizontal + vertical + cross


def picker_service_time_units(qty: int) -> float:
    SCAN_TOTE = 1.2
    SCAN_LOCATION = 1.0
    GRAB_PER_UNIT = 0.6
    DROP_FIXED = 0.5

    return SCAN_TOTE + SCAN_LOCATION + DROP_FIXED + (GRAB_PER_UNIT * qty)


# =========================================================
# SIMULATION
# =========================================================

def simulate(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["pick_time"] = df["Quantity Processed"].apply(
        picker_service_time_units
    )
    df["travel_time"] = 0.0

    for picker_id, idxs in df.groupby("picker_id").groups.items():
        idxs = list(idxs)
        for i in range(1, len(idxs)):
            df.at[idxs[i], "travel_time"] = travel_time_units(
                df.loc[idxs[i - 1]], df.loc[idxs[i]]
            )

    df["total_time"] = df["pick_time"] + df["travel_time"]
    df["cumulative_time_by_picker"] = (
        df.groupby("picker_id")["total_time"].cumsum()
    )

    return df


# =========================================================
# VISUAL OFFSETS
# =========================================================

def apply_visual_offsets(
    df,
    *,
    module_offset_x=None,
    floor_offset_y=None,
):
    if module_offset_x is None:
        module_offset_x = {"1": 0, "2": 120}

    if floor_offset_y is None:
        floor_offset_y = {1: 0, 2: 80}

    df = df.copy()
    df["pick_module"] = df["pick_module"].astype(str)

    module_offset = df["pick_module"].map(module_offset_x)
    floor_offset = df["floor"].map(floor_offset_y)

    df["x_plot"] = df["x"] + module_offset
    df["y_plot"] = df["y"] + floor_offset

    return df


# =========================================================
# CAPACITY MODEL
# =========================================================

def compute_hourly_capacity_from_picks(
    df_sim: pd.DataFrame,
    *,
    employees: int,
    time_col="Created On",
    units_col="Quantity Processed",
    freq="1h",
    min_units_per_row=1,
):
    """
    Phase 2 capacity model:
    - Uses simulated time per unit (pick_time + travel_time) to estimate capacity.
    - Uses scenario employees to compute available labor seconds per hour.

    Returns per-hour:
      - demand_units
      - avg_seconds_per_unit
      - capacity_units
      - achieved_units
      - utilization
    """

    df = df_sim.copy()

    # Ensure datetime
    if not pd.api.types.is_datetime64_any_dtype(df[time_col]):
        df[time_col] = pd.to_datetime(df[time_col], errors="coerce")

    df = df.dropna(subset=[time_col])

    # Bucket
    df["bucket_start"] = df[time_col].dt.floor(freq)

    # Guard against zeros
    df[units_col] = pd.to_numeric(df[units_col], errors="coerce").fillna(0).astype(float)
    df = df[df[units_col] >= min_units_per_row].copy()

    # Total seconds per row (Phase 2: both are seconds)
    df["row_seconds"] = df["pick_time"].astype(float) + df["travel_time"].astype(float)

    # Seconds per unit per row (weighted later)
    df["sec_per_unit_row"] = df["row_seconds"] / df[units_col]

    # Demand per bucket
    demand = df.groupby("bucket_start")[units_col].sum().rename("demand_units")

    # Weighted avg seconds/unit per bucket (weight by units)
    # avg_sec_per_unit = sum(seconds) / sum(units)
    sec_sum = df.groupby("bucket_start")["row_seconds"].sum().rename("total_seconds")
    unit_sum = df.groupby("bucket_start")[units_col].sum().rename("total_units")

    avg_sec_per_unit = (sec_sum / unit_sum).rename("avg_seconds_per_unit")

    out = pd.concat([demand, avg_sec_per_unit], axis=1).fillna(0)

    # Available labor seconds per hour
    out["available_seconds"] = employees * 3600.0

    # Capacity units per hour
    # If avg_seconds_per_unit is 0 (bad data), capacity should be 0 to avoid inf
    out["capacity_units"] = out.apply(
        lambda r: (r["available_seconds"] / r["avg_seconds_per_unit"]) if r["avg_seconds_per_unit"] > 0 else 0.0,
        axis=1,
    )

    # Achieved throughput and utilization
    out["achieved_units"] = out[["demand_units", "capacity_units"]].min(axis=1)
    out["utilization"] = out.apply(
        lambda r: (r["achieved_units"] / r["capacity_units"]) if r["capacity_units"] > 0 else 0.0,
        axis=1,
    )

    return out.reset_index()

def print_capacity_report(
    df_sim: pd.DataFrame,
    *,
    n_employees: int,
    freq="1h",
):
    hourly = compute_hourly_capacity_from_picks(
        df_sim,
        employees=n_employees,
        freq=freq,
    )

    actual_units_per_hour = float(hourly["achieved_units"].mean())
    max_units_per_hour = float(hourly["capacity_units"].mean())
    utilization = float(actual_units_per_hour / max_units_per_hour) if max_units_per_hour > 0 else 0.0

    metrics = {
        "actual_units_per_hour": actual_units_per_hour,
        "max_units_per_hour": max_units_per_hour,
        "utilization": utilization,
        "avg_seconds_per_unit": float(hourly["avg_seconds_per_unit"].mean()),
    }

    print("\nPHASE 2 UTILIZATION METRICS:")
    print(f"  employees: {n_employees}")
    print(f"  actual_units_per_hour: {metrics['actual_units_per_hour']:,.2f}")
    print(f"  max_units_per_hour   : {metrics['max_units_per_hour']:,.2f}")
    print(f"  utilization          : {metrics['utilization']:.4f}")
    print(f"  avg_seconds_per_unit : {metrics['avg_seconds_per_unit']:.2f} sec/unit")

    return metrics, hourly


# =========================================================
# MATPLOTLIB VISUAL
# =========================================================

def plot_spatial_all_pickers(df_sim):
    df = (
        df_sim.dropna(subset=["x_plot", "y_plot"])
        .sort_values("Created On")
    )

    fig, ax = plt.subplots(figsize=(10, 8))

    for pid, g in df.groupby("picker_id"):
        ax.plot(g["x_plot"], g["y_plot"], marker="o", alpha=0.6)

    ax.invert_yaxis()
    ax.grid(True, alpha=0.3)
    ax.set_title("Spatial Picking Simulation – All Pickers")

    plt.tight_layout()
    plt.show()


# =========================================================
# PYGAME VISUAL
# =========================================================

def run_pygame_simulation(df_sim):

    df = (
        df_sim.dropna(subset=["x_plot", "y_plot"])
        .sort_values("Created On")
        .reset_index(drop=True)
    )

    if df.empty:
        print("No rows to animate.")
        return

    pygame.init()
    screen = pygame.display.set_mode((1000, 700))
    clock = pygame.time.Clock()

    x_min, x_max = df["x_plot"].min(), df["x_plot"].max()
    y_min, y_max = df["y_plot"].min(), df["y_plot"].max()

    def to_screen(x, y):
        sx = int(50 + (x - x_min) * 4)
        sy = int(50 + (y - y_min) * 4)
        return sx, sy

    idx = 0
    running = True

    while running:
        clock.tick(60)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        screen.fill((245, 245, 245))

        if idx < len(df):
            row = df.iloc[idx]
            sx, sy = to_screen(row["x_plot"], row["y_plot"])
            pygame.draw.circle(screen, (200, 0, 0), (sx, sy), 5)
            idx += 1

        pygame.display.flip()

    pygame.quit()


# =========================================================
# RUN ONE SCENARIO
# =========================================================

def run_single_scenario(cfg: dict) -> dict:

    print("\n" + "=" * 70)
    print(f"RUNNING: {cfg['name']}")
    print("=" * 70)

    # Load layouts formed by slotting strategies
    df_layout = load_layout_for_strategy(cfg["slot_strategy"])
    
    # Load picks
    df_picks = load_picks_universal(cfg["picks_path"])

    df_merged = df_picks.merge(
        df_layout,
        on="location_id",
        how="left",
        validate="many_to_one",
    )

    # --------------------------
    # Simulate mod picking data
    # --------------------------
    df_sim = simulate(df_merged)
    df_sim = apply_visual_offsets(df_sim)

    metrics, hourly_df = print_capacity_report(
        df_sim,
        n_employees=cfg["employees"],
    )

    # VISUALS (toggle as needed)
    # plot_spatial_all_pickers(df_sim)
    # run_pygame_simulation(df_sim)

    return {
        "scenario": cfg["name"],
        "slot_strategy": cfg["slot_strategy"]["name"],
        "employees": cfg["employees"],
        **metrics,
    }


# =========================================================
# MAIN LOOP
# =========================================================

if __name__ == "__main__":

    results = []

    for cfg in SCENARIOS:
        try:
            out = run_single_scenario(cfg)
            results.append(out)
        except Exception as e:
            print(f"Failed: {cfg['name']}")
            print(e)

    if results:
        df_compare = pd.DataFrame(results)

        # -----------------------------
        # Ensure numeric
        # -----------------------------
        num_cols = [
            "actual_units_per_hour",
            "max_units_per_hour",
            "utilization",
            "avg_seconds_per_unit",
        ]

        for c in num_cols:
            if c in df_compare.columns:
                df_compare[c] = pd.to_numeric(df_compare[c], errors="coerce")

        # -----------------------------
        # Pretty sort
        # -----------------------------
        df_compare = df_compare.sort_values(
            ["slot_strategy", "employees"]
        ).reset_index(drop=True)

        print("\n" + "=" * 80)
        print("STAFFING COMPARISON — BY SLOTTING STRATEGY")
        print("=" * 80)

        # -----------------------------
        # Print per slot strategy
        # -----------------------------
        for strat, g in df_compare.groupby("slot_strategy"):
            print("\n" + "-" * 80)
            print(f"SLOTTING STRATEGY: {strat}")
            print("-" * 80)

            display_cols = [
                "employees",
                "actual_units_per_hour",
                "max_units_per_hour",
                "utilization",
                "avg_seconds_per_unit",
            ]

            g_print = g[display_cols].copy()

            # rounding for readability
            g_print["actual_units_per_hour"] = g_print["actual_units_per_hour"].round(0)
            g_print["max_units_per_hour"] = g_print["max_units_per_hour"].round(0)
            g_print["utilization"] = g_print["utilization"].round(4)
            g_print["avg_seconds_per_unit"] = g_print["avg_seconds_per_unit"].round(3)

            print(g_print.to_string(index=False))

        # -----------------------------
        # Save clean output
        # -----------------------------
        #df_compare.to_csv("staffing_sensitivity_clean.csv", index=False)
        #print("\nSaved: staffing_sensitivity_clean.csv")

