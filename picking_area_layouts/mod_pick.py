"""
Spatial 3-Stack Warehouse Grid (Pure Matrix per Floor)
-----------------------------------------------------
- You have 2 pick modules (PM1, PM2), each with 2 floors (1, 2).
- Each pick location is represented by a vertical stack of 3 cells in the grid:
    (x, y_top)   : location_id
    (x, y_top+1) : item_id
    (x, y_top+2) : quantity (int)

- Everything is "pure grid": a 2D matrix of Cells per floor.
- Spatial: each floor has a global origin offset (origin_x, origin_y) + z level (floor)
          so modules/floors can be placed in one global coordinate system.

Features:
- Define layout regions (VOID/AISLE/LABEL/etc.)
- Define rack regions using the 3-stack pattern with automatic location IDs
- Put/remove inventory (one SKU per location)
- Auto-putaway nearest to a reference point (spatial) with optional top-off
- Find item locations and total quantity
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict, Iterable, Iterator
import heapq
from itertools import count


# -----------------------------
# Cell kinds (encoding)
# -----------------------------
VOID = "void"            # whitespace / not part of layout
AISLE = "aisle"          # walkable or non-storage area
LABEL = "label"          # text/annotation area (ignored for storage)
BLOCKED = "blocked"      # cannot store (e.g., structural gaps)

LOC_TOP = "loc_top"      # top of 3-vertical location stack: holds location_id
LOC_ITEM = "loc_item"    # middle: holds item_id or None
LOC_QTY = "loc_qty"      # bottom: holds qty (int)


# -----------------------------
# Data classes
# -----------------------------
@dataclass
class Cell:
    kind: str
    value: Optional[object] = None


@dataclass(frozen=True)
class LocRef:
    """Logical storage location reference (anchored at the TOP of the 3-cell stack)."""
    module: str   # "PM1" or "PM2"
    floor: int    # 1 or 2
    x: int
    y_top: int


# -----------------------------
# Floor grid with 3-cell location stacks
# -----------------------------
class Floor3StackGrid:
    """
    A pure 2D matrix. Each logical storage location occupies 3 vertical cells.
    """

    def __init__(self, name: str, width: int, height: int, *, origin_x: int = 0, origin_y: int = 0, z: int = 1):
        self.name = name
        self.width = width
        self.height = height
        self.origin_x = origin_x
        self.origin_y = origin_y
        self.z = z

        # Default everything to VOID until you paint structure and define racks.
        self.grid: List[List[Cell]] = [
            [Cell(VOID, None) for _ in range(width)]
            for _ in range(height)
        ]

    # ---------- Basics ----------
    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def global_xy(self, x: int, y: int) -> Tuple[int, int]:
        return (self.origin_x + x, self.origin_y + y)

    def get_cell(self, x: int, y: int) -> Cell:
        if not self.in_bounds(x, y):
            raise IndexError(f"Out of bounds: ({x},{y})")
        return self.grid[y][x]

    def set_cell(self, x: int, y: int, kind: str, value: Optional[object] = None) -> None:
        if not self.in_bounds(x, y):
            raise IndexError(f"Out of bounds: ({x},{y})")
        self.grid[y][x] = Cell(kind, value)

    # ---------- Painting structure ----------
    def fill_rect(self, x: int, y: int, w: int, h: int, kind: str, value: Optional[object] = None) -> None:
        """Mark a rectangular region with a kind (VOID/AISLE/LABEL/BLOCKED)."""
        for yy in range(max(0, y), min(self.height, y + h)):
            row = self.grid[yy]
            for xx in range(max(0, x), min(self.width, x + w)):
                row[xx] = Cell(kind, value)

    # ---------- Defining location stacks ----------
    def _stack_cells(self, x: int, y_top: int) -> Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int]]:
        return (x, y_top), (x, y_top + 1), (x, y_top + 2)

    def is_location_top(self, x: int, y: int) -> bool:
        return self.in_bounds(x, y) and self.grid[y][x].kind == LOC_TOP

    def is_part_of_location_stack(self, x: int, y: int) -> bool:
        """True if cell is any of LOC_TOP/LOC_ITEM/LOC_QTY."""
        if not self.in_bounds(x, y):
            return False
        return self.grid[y][x].kind in (LOC_TOP, LOC_ITEM, LOC_QTY)

    def define_location_stack(self, x: int, y_top: int, loc_id: str, *, force: bool = False) -> None:
        """
        Define one logical location stack at (x, y_top).

        If force=False, raises if any of the 3 cells are already part of another location stack.
        """
        if not (self.in_bounds(x, y_top) and self.in_bounds(x, y_top + 2)):
            raise ValueError(f"Location stack out of bounds for top ({x},{y_top}). Needs y_top+2 inside grid.")

        coords = self._stack_cells(x, y_top)
        if not force:
            for (xx, yy) in coords:
                if self.is_part_of_location_stack(xx, yy):
                    raise ValueError(f"Collision: cell ({xx},{yy}) already belongs to a location stack.")

        self.set_cell(x, y_top, LOC_TOP, loc_id)
        self.set_cell(x, y_top + 1, LOC_ITEM, None)
        self.set_cell(x, y_top + 2, LOC_QTY, 0)

    def define_rack_region_3stack(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        *,
        loc_prefix: str,
        start_index: int = 1,
        col_step: int = 1,
        row_step: int = 3,
        force: bool = False
    ) -> int:
        """
        Define a rectangular rack region filled with 3-stack locations.

        Coordinates are inclusive-exclusive like Python slicing:
          x in [x0, x1), y_top in [y0, y1) stepping by row_step (=3)

        Returns next index after the last assigned ID.
        """
        idx = start_index
        for x in range(x0, x1, col_step):
            for y_top in range(y0, y1, row_step):
                # Ensure we have 3 cells available vertically
                if y_top + 2 >= self.height:
                    continue
                if not self.in_bounds(x, y_top):
                    continue
                loc_id = f"{loc_prefix}{idx:06d}"
                self.define_location_stack(x, y_top, loc_id, force=force)
                idx += 1
        return idx

    def iter_locations(self) -> Iterator[Tuple[int, int, str]]:
        """Iterate logical locations as (x, y_top, loc_id)."""
        for y in range(self.height):
            for x in range(self.width):
                cell = self.grid[y][x]
                if cell.kind == LOC_TOP:
                    yield x, y, str(cell.value)

    # ---------- Inventory logic (one SKU per location) ----------
    def get_location_record(self, x: int, y_top: int) -> Tuple[str, Optional[str], int]:
        """Return (loc_id, item_id, qty)."""
        if not self.is_location_top(x, y_top):
            raise ValueError(f"({x},{y_top}) is not a location top cell.")
        loc_id = str(self.grid[y_top][x].value)
        item_id = self.grid[y_top + 1][x].value
        qty = int(self.grid[y_top + 2][x].value)
        return loc_id, item_id, qty

    def is_empty_location(self, x: int, y_top: int) -> bool:
        _, item_id, qty = self.get_location_record(x, y_top)
        return item_id is None and qty == 0

    def put_into_location(self, x: int, y_top: int, item_id: str, qty: int, *, max_qty: int = 9999) -> str:
        """
        Put qty of item_id into the location.
        - If empty: sets item_id and qty
        - If same item: adds qty
        - If different item: error
        """
        if qty <= 0:
            raise ValueError("qty must be > 0")

        loc_id, cur_item, cur_qty = self.get_location_record(x, y_top)

        if cur_item is None:
            if qty > max_qty:
                raise ValueError(f"qty {qty} exceeds max_qty {max_qty} for location {loc_id}")
            self.grid[y_top + 1][x].value = item_id
            self.grid[y_top + 2][x].value = qty
            return loc_id

        if cur_item != item_id:
            raise ValueError(f"Location {loc_id} holds {cur_item}; cannot place {item_id}")

        new_qty = cur_qty + qty
        if new_qty > max_qty:
            raise ValueError(f"Would exceed max_qty {max_qty} at {loc_id} (current {cur_qty}, add {qty}).")
        self.grid[y_top + 2][x].value = new_qty
        return loc_id

    def remove_from_location(self, x: int, y_top: int, qty: int) -> str:
        """Remove qty; if qty becomes 0, clears SKU."""
        if qty <= 0:
            raise ValueError("qty must be > 0")

        loc_id, cur_item, cur_qty = self.get_location_record(x, y_top)
        if cur_item is None:
            raise ValueError(f"Location {loc_id} is empty.")
        if qty > cur_qty:
            raise ValueError(f"Not enough inventory at {loc_id}. Have {cur_qty}, requested {qty}")

        remaining = cur_qty - qty
        self.grid[y_top + 2][x].value = remaining
        if remaining == 0:
            self.grid[y_top + 1][x].value = None
        return loc_id

    def find_item(self, item_id: str) -> List[Tuple[str, int, int, int]]:
        """Return [(loc_id, x, y_top, qty), ...] for where item_id exists."""
        out: List[Tuple[str, int, int, int]] = []
        for x, y_top, loc_id in self.iter_locations():
            _lid, cur_item, cur_qty = self.get_location_record(x, y_top)
            if cur_item == item_id and cur_qty > 0:
                out.append((loc_id, x, y_top, cur_qty))
        return out

    def total_qty(self, item_id: str) -> int:
        return sum(qty for _, _, _, qty in self.find_item(item_id))


# -----------------------------
# Warehouse container (PM1/PM2 x floors) with spatial auto-putaway
# -----------------------------
class Warehouse3Stack:
    def __init__(self):
        self.floors: Dict[str, Dict[int, Floor3StackGrid]] = {}

    def add_floor(self, module: str, floor_num: int, floor: Floor3StackGrid) -> None:
        self.floors.setdefault(module, {})[floor_num] = floor

    def floor(self, module: str, floor_num: int) -> Floor3StackGrid:
        return self.floors[module][floor_num]

    # ---------- Spatial distance ----------
    def distance(self, a: LocRef, b: LocRef, *, floor_penalty: int = 50) -> int:
        fa = self.floor(a.module, a.floor)
        fb = self.floor(b.module, b.floor)
        ax, ay = fa.global_xy(a.x, a.y_top)
        bx, by = fb.global_xy(b.x, b.y_top)
        return abs(ax - bx) + abs(ay - by) + abs(fa.z - fb.z) * floor_penalty

    # ---------- Inventory actions across warehouse ----------
    def put_at(self, loc: LocRef, item_id: str, qty: int, *, max_qty: int = 9999) -> str:
        return self.floor(loc.module, loc.floor).put_into_location(loc.x, loc.y_top, item_id, qty, max_qty=max_qty)

    def remove_at(self, loc: LocRef, qty: int) -> str:
        return self.floor(loc.module, loc.floor).remove_from_location(loc.x, loc.y_top, qty)

    def find_item(self, item_id: str) -> List[Tuple[LocRef, str, int]]:
        """
        Return [(LocRef, loc_id, qty), ...] across all modules/floors.
        """
        out: List[Tuple[LocRef, str, int]] = []
        for mod, fls in self.floors.items():
            for fnum, fl in fls.items():
                for loc_id, x, y_top, qty in [(lid, x, y, q) for (lid, x, y, q) in fl.find_item(item_id)]:
                    out.append((LocRef(mod, fnum, x, y_top), loc_id, qty))
        return out

    def total_qty(self, item_id: str) -> int:
        return sum(qty for _, _, qty in self.find_item(item_id))

    # ---------- Auto putaway ----------
    def put_auto_nearest(
        self,
        item_id: str,
        qty: int,
        reference: LocRef,
        *,
        restrict_module: Optional[str] = None,
        restrict_floor: Optional[int] = None,
        max_qty_per_location: int = 9999,
        allow_spread: bool = True,
        top_off_existing: bool = True,
        floor_penalty: int = 50,
    ) -> List[Tuple[LocRef, int]]:
        """
        Spatial putaway near 'reference'.
        Strategy:
          1) (optional) top-off existing locations that already hold item_id (nearest first)
          2) fill empty locations (nearest first)
        Returns [(LocRef, qty_placed), ...]
        """
        if qty <= 0:
            return []

        # Helper to iterate candidate locations
        def iter_candidate_locations() -> Iterable[LocRef]:
            for mod, fls in self.floors.items():
                if restrict_module and mod != restrict_module:
                    continue
                for fnum, fl in fls.items():
                    if restrict_floor and fnum != restrict_floor:
                        continue
                    for x, y_top, _loc_id in fl.iter_locations():
                        yield LocRef(mod, fnum, x, y_top)

        # Build heaps for (distance, loc)
        tie = count()  # unique increasing integers to break distance ties

        existing_heap: List[Tuple[int, int, LocRef]] = []
        empty_heap: List[Tuple[int, int, LocRef]] = []

        for loc in iter_candidate_locations():
            fl = self.floor(loc.module, loc.floor)
            loc_id, cur_item, cur_qty = fl.get_location_record(loc.x, loc.y_top)

            d = self.distance(reference, loc, floor_penalty=floor_penalty)

            if cur_item == item_id and cur_qty < max_qty_per_location:
                existing_heap.append((d, next(tie), loc))
            elif cur_item is None and cur_qty == 0:
                empty_heap.append((d, next(tie), loc))

        heapq.heapify(existing_heap)
        heapq.heapify(empty_heap)

        remaining = qty
        placed: List[Tuple[LocRef, int]] = []

        # 1) top off existing (nearest)
        if top_off_existing:
            while remaining > 0 and existing_heap:
                _, _, loc = heapq.heappop(existing_heap)
                fl = self.floor(loc.module, loc.floor)
                loc_id, cur_item, cur_qty = fl.get_location_record(loc.x, loc.y_top)

                space = max_qty_per_location - cur_qty
                if space <= 0:
                    continue

                put_qty = min(space, remaining)
                fl.put_into_location(loc.x, loc.y_top, item_id, put_qty, max_qty=max_qty_per_location)
                placed.append((loc, put_qty))
                remaining -= put_qty

                if remaining > 0 and not allow_spread:
                    # If not allowed to spread, we only consider "one location" total.
                    # But topping off might have used one location; still can't finish.
                    raise ValueError("allow_spread=False and not enough space in one location.")

        # 2) fill empty (nearest)
        while remaining > 0 and empty_heap:
            _, _, loc = heapq.heappop(empty_heap)
            fl = self.floor(loc.module, loc.floor)

            put_qty = min(max_qty_per_location, remaining)
            fl.put_into_location(loc.x, loc.y_top, item_id, put_qty, max_qty=max_qty_per_location)
            placed.append((loc, put_qty))
            remaining -= put_qty

            if remaining > 0 and not allow_spread:
                raise ValueError("allow_spread=False and not enough space in one empty location.")

        if remaining > 0:
            raise ValueError(f"Not enough capacity: remaining qty={remaining} for item {item_id}")

        return placed


# -----------------------------
# Example template builder
# -----------------------------
def build_template_2_modules_2_floors(
    *,
    width: int,
    height: int,
    pm1_origin: Tuple[int, int] = (0, 0),
    pm2_origin: Tuple[int, int] = (600, 0),
    floor_y_gap: int = 250,
) -> Warehouse3Stack:
    """
    Creates a Warehouse with PM1/PM2, floors 1/2, with spatial origins.
    You still need to paint regions and define rack regions per your layout.
    """
    wh = Warehouse3Stack()

    pm1_x0, pm1_y0 = pm1_origin
    pm2_x0, pm2_y0 = pm2_origin

    # Floor 1 origins (z=1)
    wh.add_floor("PM1", 1, Floor3StackGrid("PM1-F1", width, height, origin_x=pm1_x0, origin_y=pm1_y0, z=1))
    wh.add_floor("PM2", 1, Floor3StackGrid("PM2-F1", width, height, origin_x=pm2_x0, origin_y=pm2_y0, z=1))

    # Floor 2 origins (z=2) shifted in global y for spatial separation
    wh.add_floor("PM1", 2, Floor3StackGrid("PM1-F2", width, height, origin_x=pm1_x0, origin_y=pm1_y0 + floor_y_gap, z=2))
    wh.add_floor("PM2", 2, Floor3StackGrid("PM2-F2", width, height, origin_x=pm2_x0, origin_y=pm2_y0 + floor_y_gap, z=2))

    return wh


# -----------------------------
# Minimal example usage
# -----------------------------
if __name__ == "__main__":
    # Adjust width/height to match your Excel grid resolution (columns x rows).
    W, H = 320, 90

    wh = build_template_2_modules_2_floors(width=W, height=H)

    # Example: paint everything VOID initially (already is), then mark an aisle band.
    pm2_f2 = wh.floor("PM2", 2)
    pm2_f2.fill_rect(0, 40, W, 3, AISLE)   # e.g., main aisle band
    pm2_f2.fill_rect(0, 0, 10, H, BLOCKED) # e.g., left padding/labels area

    # Define a "rack region" on PM2 floor 2 using 3-stack pattern.
    # Here we define locations from x=10..250, y_top=10..34, stepping rows by 3.
    next_id = pm2_f2.define_rack_region_3stack(
        x0=10, y0=10, x1=250, y1=34,
        loc_prefix="PM2F2-",
        start_index=1
    )

    # Define a second rack region (bottom run)
    next_id = pm2_f2.define_rack_region_3stack(
        x0=10, y0=50, x1=250, y1=80,
        loc_prefix="PM2F2-",
        start_index=next_id
    )

    # Put a SKU into a specific location top coordinate (x=10, y_top=10)
    loc_specific = LocRef("PM2", 2, x=10, y_top=10)
    wh.put_at(loc_specific, "SKU-123", 25, max_qty=100)

    # Auto-putaway near a reference point (spatial)
    reference = LocRef("PM2", 2, x=140, y_top=10)  # near "conveyor area" for example
    placements = wh.put_auto_nearest(
        "SKU-ABC",
        235,
        reference,
        restrict_module="PM2",
        restrict_floor=2,
        max_qty_per_location=100,
        allow_spread=True,
        top_off_existing=True
    )

    print("Placed SKU-ABC into:")
    for loc, q in placements:
        fl = wh.floor(loc.module, loc.floor)
        loc_id, item_id, qty = fl.get_location_record(loc.x, loc.y_top)
        print(f"  {loc} -> {loc_id}: {item_id} qty={qty} (placed {q})")

    print("Total SKU-ABC:", wh.total_qty("SKU-ABC"))

    # Find where SKU-123 is
    print("SKU-123 locations:", wh.find_item("SKU-123"))
