from __future__ import annotations

import pandas as pd


REQUIRED_COLS = ["module", "floor", "x", "y", "loc_id", "item_id", "qty", "max_qty"]


def load_bins_csv_to_df(path: str, *, validate: bool = True) -> pd.DataFrame:
    """
    Load bins.csv into a pandas DataFrame with clean types + optional validation.

    Expected header (exact columns):
      module,floor,x,y,loc_id,item_id,qty,max_qty

    Conventions:
    - blank item_id -> empty (stored as None)
    - qty/max_qty/floor/x/y are ints
    """
    # Read everything as string first to avoid pandas guessing wrong types
    df = pd.read_csv(path, dtype=str).fillna("")

    # Column check (order doesn't matter)
    missing = set(REQUIRED_COLS) - set(df.columns)
    extra = set(df.columns) - set(REQUIRED_COLS)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    if extra:
        raise ValueError(f"Unexpected extra columns: {sorted(extra)}")

    # Normalize whitespace
    for c in REQUIRED_COLS:
        df[c] = df[c].astype(str).str.strip()

    # Convert numeric columns
    for c in ["floor", "x", "y", "qty", "max_qty"]:
        # allow empty -> error later if validate=True
        df[c] = pd.to_numeric(df[c], errors="raise").astype(int)

    # item_id: blank -> None (so empty bins are obvious)
    df["item_id"] = df["item_id"].replace({"": None})

    if validate:
        # basic content checks
        if (df["loc_id"] == "").any():
            bad = df.index[df["loc_id"] == ""].tolist()[:10]
            raise ValueError(f"Blank loc_id found in rows: {bad} (showing up to 10)")

        if (df["qty"] < 0).any():
            raise ValueError("qty cannot be negative")
        if (df["max_qty"] <= 0).any():
            raise ValueError("max_qty must be > 0")
        if (df["qty"] > df["max_qty"]).any():
            raise ValueError("qty cannot exceed max_qty")

        # qty/item_id consistency
        bad1 = df[(df["qty"] == 0) & (df["item_id"].notna())]
        if not bad1.empty:
            raise ValueError("Found rows where qty==0 but item_id is not blank")

        bad2 = df[(df["qty"] > 0) & (df["item_id"].isna())]
        if not bad2.empty:
            raise ValueError("Found rows where qty>0 but item_id is blank")

        # uniqueness checks
        if df["loc_id"].duplicated().any():
            dups = df.loc[df["loc_id"].duplicated(), "loc_id"].unique()[:10]
            raise ValueError(f"Duplicate loc_id(s): {dups}")

        coord_cols = ["module", "floor", "x", "y"]
        if df.duplicated(subset=coord_cols).any():
            dups = df.loc[df.duplicated(subset=coord_cols), coord_cols].head(10)
            raise ValueError(f"Duplicate (module,floor,x,y) rows detected:\n{dups}")

    # Optional: set a useful index (coordinates) for fast lookup
    df = df.set_index(["module", "floor", "x", "y"]).sort_index()

    return df

if __name__ == "__main__":
    # If bins.csv is one level above where you run the script:
    df_bins = load_bins_csv_to_df("../bins.csv", validate=True)

    print(df_bins.head())
    # Example lookup:
    # print(df_bins.loc[("PM2", 2, 12, 7)])
