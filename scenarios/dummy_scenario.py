"""
Basic Warehouse Simulation (SimPy)
- Inbound: receiving + putaway increases inventory
- Outbound: orders consume inventory, then pick -> pack -> ship
- Orders can backorder (wait) if inventory is insufficient
"""

import random
import statistics
import simpy
from dataclasses import dataclass, field
from collections import defaultdict

# -----------------------------
# Config
# -----------------------------
RANDOM_SEED = 42
SIM_HOURS = 8
SIM_TIME = SIM_HOURS * 60  # minutes

SKU_LIST = ["A", "B", "C", "D"]
INITIAL_STOCK = {"A": 80, "B": 60, "C": 50, "D": 40}

# Resources (capacity = number of parallel servers)
N_RECEIVING_DOCKS = 1
N_FORKLIFTS = 1
N_PICKERS = 2
N_PACKERS = 1
N_SHIP_DOCKS = 1

# Arrival processes
MEAN_ORDER_IAT = 6.0      # mean interarrival time of customer orders (minutes)
MEAN_INBOUND_IAT = 30.0   # mean interarrival time of inbound shipments (minutes)

# Service time settings (minutes)
PICK_BASE = 3.0          # base pick time per order
PICK_PER_LINE = 1.5      # extra pick time per line item
PACK_BASE = 2.5
PACK_PER_LINE = 1.0
SHIP_TIME = 2.0

RECEIVE_TIME = (6, 12)   # uniform(min,max) receiving time
PUTAWAY_TIME = (5, 10)   # uniform(min,max) putaway time


# -----------------------------
# Data structures
# -----------------------------
@dataclass
class Order:
    oid: int
    arrival: float
    lines: dict  # sku -> qty

@dataclass
class Stats:
    completed_orders: int = 0
    cycle_times: list = field(default_factory=list)
    backorder_waits: list = field(default_factory=list)  # time spent waiting for inventory
    wait_times: dict = field(default_factory=lambda: defaultdict(list))  # stage -> waits
    busy_time: dict = field(default_factory=lambda: defaultdict(float))  # stage -> busy minutes


# -----------------------------
# Helper functions
# -----------------------------
def exp_time(mean):
    return random.expovariate(1.0 / mean)

def make_random_order(oid, now):
    # 1-4 line items, each 1-5 qty
    n_lines = random.randint(1, 4)
    skus = random.sample(SKU_LIST, k=n_lines)
    lines = {sku: random.randint(1, 5) for sku in skus}
    return Order(oid=oid, arrival=now, lines=lines)

def use_resource(env, resource, duration, stats, stage_name):
    """Request a 1-unit resource, record queue wait + busy time."""
    t_req = env.now
    with resource.request() as req:
        yield req
        t_start = env.now
        stats.wait_times[stage_name].append(t_start - t_req)
        yield env.timeout(duration)
        stats.busy_time[stage_name] += duration


# -----------------------------
# Processes
# -----------------------------
def order_generator(env, order_store):
    oid = 0
    while True:
        yield env.timeout(exp_time(MEAN_ORDER_IAT))
        oid += 1
        order = make_random_order(oid, env.now)
        yield order_store.put(order)

def inbound_generator(env, inbound_store):
    sid = 0
    while True:
        yield env.timeout(exp_time(MEAN_INBOUND_IAT))
        sid += 1
        sku = random.choice(SKU_LIST)
        qty = random.randint(20, 60)
        yield inbound_store.put((sid, sku, qty))

def inbound_process(env, inbound_store, inv, receiving_dock, forklift, stats):
    while True:
        sid, sku, qty = yield inbound_store.get()

        # receiving
        recv_dur = random.uniform(*RECEIVE_TIME)
        yield from use_resource(env, receiving_dock, recv_dur, stats, "receiving")

        # putaway
        put_dur = random.uniform(*PUTAWAY_TIME)
        yield from use_resource(env, forklift, put_dur, stats, "putaway")

        # inventory increases after putaway
        yield inv[sku].put(qty)

def fulfill_order(env, order, inv, pickers, packers, ship_dock, stats):
    # --- inventory reservation / backorder wait ---
    backorder_start = env.now
    for sku, qty in order.lines.items():
        # blocks until enough inventory exists
        yield inv[sku].get(qty)
    stats.backorder_waits.append(env.now - backorder_start)

    n_lines = len(order.lines)

    # --- pick ---
    pick_time = PICK_BASE + PICK_PER_LINE * n_lines + random.random() * 2.0
    yield from use_resource(env, pickers, pick_time, stats, "picking")

    # --- pack ---
    pack_time = PACK_BASE + PACK_PER_LINE * n_lines + random.random() * 1.5
    yield from use_resource(env, packers, pack_time, stats, "packing")

    # --- ship ---
    yield from use_resource(env, ship_dock, SHIP_TIME, stats, "shipping")

    # complete
    stats.completed_orders += 1
    stats.cycle_times.append(env.now - order.arrival)

def outbound_process(env, order_store, inv, pickers, packers, ship_dock, stats):
    while True:
        order = yield order_store.get()
        env.process(fulfill_order(env, order, inv, pickers, packers, ship_dock, stats))


# -----------------------------
# Run simulation
# -----------------------------
def run():
    random.seed(RANDOM_SEED)
    env = simpy.Environment()

    # Inventory as Containers (one per SKU)
    inv = {sku: simpy.Container(env, init=INITIAL_STOCK[sku], capacity=10_000) for sku in SKU_LIST}

    # Resources
    receiving_dock = simpy.Resource(env, capacity=N_RECEIVING_DOCKS)
    forklift = simpy.Resource(env, capacity=N_FORKLIFTS)
    pickers = simpy.Resource(env, capacity=N_PICKERS)
    packers = simpy.Resource(env, capacity=N_PACKERS)
    ship_dock = simpy.Resource(env, capacity=N_SHIP_DOCKS)

    # Stores
    order_store = simpy.Store(env)
    inbound_store = simpy.Store(env)

    stats = Stats()

    # Start processes
    env.process(order_generator(env, order_store))
    env.process(inbound_generator(env, inbound_store))
    env.process(inbound_process(env, inbound_store, inv, receiving_dock, forklift, stats))
    env.process(outbound_process(env, order_store, inv, pickers, packers, ship_dock, stats))

    # Run
    env.run(until=SIM_TIME)

    # -----------------------------
    # Report
    # -----------------------------
    def avg(x): return statistics.mean(x) if x else 0.0
    def pct(x, p): return statistics.quantiles(x, n=100)[p-1] if len(x) >= 100 else (sorted(x)[int(len(x)*p/100)] if x else 0.0)

    print(f"\n--- Warehouse Simulation Results ({SIM_HOURS} hours) ---")
    print(f"Completed orders: {stats.completed_orders}")
    if stats.cycle_times:
        print(f"Avg order cycle time (min): {avg(stats.cycle_times):.2f}")
        print(f"P90 order cycle time (min): {pct(stats.cycle_times, 90):.2f}")
    if stats.backorder_waits:
        print(f"Avg inventory wait/backorder (min): {avg(stats.backorder_waits):.2f}")
        print(f"P90 inventory wait/backorder (min): {pct(stats.backorder_waits, 90):.2f}")

    # Utilization (busy / (capacity * sim_time))
    util = {
        "receiving": stats.busy_time["receiving"] / (N_RECEIVING_DOCKS * SIM_TIME),
        "putaway":   stats.busy_time["putaway"]   / (N_FORKLIFTS * SIM_TIME),
        "picking":   stats.busy_time["picking"]   / (N_PICKERS * SIM_TIME),
        "packing":   stats.busy_time["packing"]   / (N_PACKERS * SIM_TIME),
        "shipping":  stats.busy_time["shipping"]  / (N_SHIP_DOCKS * SIM_TIME),
    }
    print("\nUtilization:")
    for k, v in util.items():
        print(f"  {k:9s}: {100*v:5.1f}%")

    print("\nAvg queue waits (min):")
    for stage in ["receiving", "putaway", "picking", "packing", "shipping"]:
        print(f"  {stage:9s}: {avg(stats.wait_times[stage]):.2f}")

    print("\nEnding inventory:")
    for sku in SKU_LIST:
        print(f"  {sku}: {inv[sku].level:.0f}")

if __name__ == "__main__":
    # If needed: pip install simpy
    run()
