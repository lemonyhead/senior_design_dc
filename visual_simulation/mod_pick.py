"""
Full Spatial Picking Simulation + Visualization
===============================================

WHAT THIS SCRIPT DOES
---------------------
1) Imports df_part1 (layout / heatmap output) from picking_area_layouts/mod_pick.py
2) Loads pick data from Excel:
   "Picks (Mod) - Week of 1-12-26"
3) Merges spatial coordinates onto picks
4) Simulates travel + pick time
5) Visualizes the FULL simulation spatially:
   - static paths
   - animated paths (single picker + all pickers)

TRAVEL MODEL
------------
- Horizontal (x): 1.0 time unit per unit
- Vertical (y): 0.5 time units if y changes
- Floor OR module change: +5.0 time units
- NaN-safe (missing layout => travel = 0)

PICK MODEL
----------
- 3.0 fixed
- +0.4 per unit
"""

from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import itertools


# =========================================================
# 0) IMPORT LAYOUT DATA (df_part1) FROM mod_pick.py
# =========================================================
BASE_DIR = Path(__file__).resolve().parent
LAYOUT_DIR = (BASE_DIR / ".." / "picking_area_layouts").resolve()
sys.path.insert(0, str(LAYOUT_DIR))

import mod_pick  # noqa

# EXPECTED columns in df_part1:
# ["pick_module", "floor", "x", "y", "location_id", "sku", "quantity"]
df_part1 = mod_pick.df_part1

# =========================================================
# 1) LOAD PICKS FROM EXCEL
# =========================================================
def load_picks_excel(path: str) -> pd.DataFrame:
    df = pd.read_excel(
        path,
        sheet_name="Picks (Mod) - Week of 1-12-26",
        dtype=str
    ).fillna("")

    df.columns = [c.strip() for c in df.columns]

    df["Created On"] = pd.to_datetime(df["Created On"], errors="raise")
    df["Quantity Processed"] = (
        pd.to_numeric(df["Quantity Processed"], errors="raise")
        .astype(int)
    )

    # Picker ID
    if "User Name" in df.columns and (df["User Name"] != "").any():
        df["picker_id"] = df["User Name"]
    else:
        df["picker_id"] = df["Full Name"]

    # Location join key
    df["location_id"] = (
        df["From Location (Pick Module)"]
        .astype(str)
        .str.strip()
    )

    df = df.sort_values(["picker_id", "Created On"]).reset_index(drop=True)
    return df


# =========================================================
# 2) PREPARE LAYOUT DATA
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
# 3) TIME MODELS (NaN-safe)
# =========================================================
def travel_time_units(prev_row: pd.Series, row: pd.Series) -> float:
    required = ["x", "y", "floor", "pick_module"]
    if any(pd.isna(prev_row[c]) or pd.isna(row[c]) for c in required):
        return 0.0

    horizontal = abs(row["x"] - prev_row["x"]) * 1.0
    vertical = 0.5 if row["y"] != prev_row["y"] else 0.0
    cross = (
        5.0
        if (row["floor"] != prev_row["floor"]) or
           (row["pick_module"] != prev_row["pick_module"])
        else 0.0
    )
    return horizontal + vertical + cross


def pick_time_units(qty: int) -> float:
    return 3.0 + 0.4 * qty


# =========================================================
# 4) SIMULATION
# =========================================================
def simulate(df: pd.DataFrame) -> pd.DataFrame:
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

def apply_visual_offsets(
    df,
    *,
    module_offset_x=None,
    floor_offset_y=None,
    x_col="x",
    y_col="y",
    module_col="pick_module",
    floor_col="floor",
):
    """
    Apply VISUAL-ONLY spatial offsets to separate pick modules and floors.

    - NaN-safe (rows with missing layout are left as NaN)
    - Does NOT modify simulation logic
    - Adds x_plot / y_plot columns for visualization only
    """

    if module_offset_x is None:
        module_offset_x = {
            "1": 0,
            "2": 120,
        }

    if floor_offset_y is None:
        floor_offset_y = {
            1: 0,
            2: 80,
        }

    df = df.copy()

    # Ensure module is string for mapping
    df[module_col] = df[module_col].astype(str)

    # DO NOT force floor to int — keep NaNs
    # Instead, map safely
    module_offset = df[module_col].map(module_offset_x)
    floor_offset = df[floor_col].map(floor_offset_y)

    # Apply offsets only where x/y exist
    df["x_plot"] = df[x_col] + module_offset
    df["y_plot"] = df[y_col] + floor_offset

    return df

# =========================================================
# 5) STATIC SPATIAL PLOTS
# =========================================================
def plot_spatial_all_pickers(df_sim):
    df = df_sim.dropna(subset=["x_plot", "y_plot"]).sort_values("Created On")
    fig, ax = plt.subplots(figsize=(8, 8))

    for pid, g in df.groupby("picker_id"):
        ax.plot(g["x_plot"], g["y_plot"], marker="o", alpha=0.6, label=str(pid))

    ax.invert_yaxis()
    ax.set_title("Spatial Simulation – All Pickers")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.grid(True, alpha=0.3)
    ax.legend(bbox_to_anchor=(1.05, 1), loc="upper left", fontsize=8)
    plt.tight_layout()
    plt.show()


def plot_spatial_picker(df_sim, picker_id):
    df = (
        df_sim[df_sim["picker_id"] == picker_id]
        .dropna(subset=["x_plot", "y_plot"])
        .sort_values("Created On")
    )
    if df.empty:
        print("No spatial data")
        return

    fig, ax = plt.subplots(figsize=(7, 7))
    ax.plot(df["x_plot"], df["y_plot"], "-o")
    ax.invert_yaxis()
    ax.set_title(f"Spatial Path – Picker {picker_id}")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.grid(True, alpha=0.3)
    plt.show()


# =========================================================
# 6) ANIMATIONS
# =========================================================
def animate_spatial_picker(df_sim, picker_id, interval=350):
    df = (
        df_sim[df_sim["picker_id"] == picker_id]
        .dropna(subset=["x_plot", "y_plot"])
        .sort_values("Created On")
        .reset_index(drop=True)
    )
    if df.empty:
        print("No spatial data")
        return

    fig, ax = plt.subplots(figsize=(7, 7))
    ax.set_xlim(df["x_plot"].min() - 1, df["x_plot"].max() + 1)
    ax.set_ylim(df["y_plot"].min() - 1, df["y_plot"].max() + 1)
    ax.invert_yaxis()
    ax.grid(True, alpha=0.3)
    ax.set_title(f"Animated Spatial Simulation – Picker {picker_id}")

    point, = ax.plot([], [], "ro")
    path, = ax.plot([], [], "-", alpha=0.5)

    xs, ys = [], []

    def update(i):
        xs.append(df.loc[i, "x_plot"])
        ys.append(df.loc[i, "y_plot"])
        point.set_data([xs[-1]], [ys[-1]])
        path.set_data(xs, ys)
        return point, path

    anim = FuncAnimation(fig, update, frames=len(df), interval=interval)
    plt.show()


def animate_spatial_all_pickers(df_sim, interval=300):
    df = df_sim.dropna(subset=["x_plot", "y_plot"]).sort_values("Created On").reset_index(drop=True)

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.set_xlim(df["x_plot"].min() - 1, df["x_plot"].max() + 1)
    ax.set_ylim(df["y_plot"].min() - 1, df["y_plot"].max() + 1)
    ax.invert_yaxis()
    ax.grid(True, alpha=0.3)
    ax.set_title("Animated Spatial Simulation – All Pickers")

    pickers = df["picker_id"].unique()
    colors = itertools.cycle(plt.cm.tab10.colors)

    points = {}
    paths = {}

    for pid, color in zip(pickers, colors):
        p, = ax.plot([], [], "o", color=color)
        l, = ax.plot([], [], "-", color=color, alpha=0.4)
        points[pid] = p
        paths[pid] = (l, [], [])

    def update(i):
        row = df.loc[i]
        pid = row["picker_id"]
        line, xs, ys = paths[pid]
        xs.append(row["x_plot"])
        ys.append(row["y_plot"])
        points[pid].set_data([xs[-1]], [ys[-1]])
        line.set_data(xs, ys)
        return list(points.values())

    anim = FuncAnimation(fig, update, frames=len(df), interval=interval)
    plt.show()


# =========================================================
# 7) RUN
# =========================================================
if __name__ == "__main__":
    EXCEL_PATH = "../data/Operations_Data_Week_of_1-12-26.xlsx"

    df_picks = load_picks_excel(EXCEL_PATH)
    df_layout = prepare_layout_df(df_part1)

    df_merged = df_picks.merge(
        df_layout,
        on="location_id",
        how="left",
        validate="many_to_one"
    )

    df_sim = simulate(df_merged)

    df_sim = apply_visual_offsets(
        df_sim,
        module_offset_x={"1": 0, "2": 120},
        floor_offset_y={1: 0, 2: 80},
    )

    # ---- VISUALS ----
    plot_spatial_all_pickers(df_sim)
    plot_spatial_picker(df_sim, picker_id=df_sim["picker_id"].iloc[0])
    #animate_spatial_picker(df_sim, picker_id=df_sim["picker_id"].iloc[0])
    #animate_spatial_all_pickers(df_sim)
