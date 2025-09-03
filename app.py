import streamlit as st
import pandas as pd
import plotly.express as px
from io import BytesIO
import math
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

# ————————————————————————————————
# 1. Page Config
# ————————————————————————————————
st.set_page_config(page_title="Grain Distribution Dashboard", layout="wide")

# ————————————————————————————————
# 2. Helpers
# ————————————————————————————————
def to_excel(df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()

def _get_setting(settings_df: pd.DataFrame, name: str, default=None, cast=float):
    try:
        val = settings_df.loc[settings_df["Parameter"] == name, "Value"].iloc[0]
        return cast(val)
    except Exception:
        return default

@st.cache_data(show_spinner=False)
def load_data_from_bytes(b: bytes):
    with BytesIO(b) as bio:
        xl = pd.ExcelFile(bio)
        need = ["Settings", "CG_to_LG", "LG_to_FPS", "Stock_Levels", "LGs", "FPS"]
        missing = [s for s in need if s not in xl.sheet_names]
        if missing:
            raise ValueError(f"Missing sheets: {missing}")

        settings     = xl.parse("Settings")
        dispatch_cg  = xl.parse("CG_to_LG")       # expects columns: Day, LG_ID, Quantity_tons, (optional Vehicle_ID)
        dispatch_lg  = xl.parse("LG_to_FPS")      # expects columns: Day, LG_ID, FPS_ID, Quantity_tons, Vehicle_ID
        stock_levels = xl.parse("Stock_Levels")   # expects columns: Day, Entity_Type, Entity_ID, Stock_Level_tons
        lgs          = xl.parse("LGs")
        fps          = xl.parse("FPS")
        return settings, dispatch_cg, dispatch_lg, stock_levels, lgs, fps

# ————————————————————————————————
# 3. File Uploader (REQUIRED)
# ————————————————————————————————
st.title("🚛 Grain Distribution Dashboard")

with st.sidebar:
    st.header("Input File (required)")
    uploaded = st.file_uploader("Upload Excel file (.xlsx) with required sheets", type=["xlsx"])

if uploaded is None:
    st.info("Please upload the simulation output Excel to view the dashboard.")
    st.stop()

# Read uploaded bytes
try:
    settings, dispatch_cg, dispatch_lg, stock_levels, lgs, fps = load_data_from_bytes(uploaded.getvalue())
    st.success(f"Loaded: {uploaded.name}")
except Exception as e:
    st.error(f"Failed to load data: {e}")
    st.stop()

# Normalize expected columns lightly (strip spaces etc.)
for df in [dispatch_cg, dispatch_lg, stock_levels, lgs, fps, settings]:
    df.columns = [str(c).strip() for c in df.columns]

# ————————————————————————————————
# 4. Compute Core Metrics (robust to missing settings)
# ————————————————————————————————
DAYS       = int(_get_setting(settings, "Distribution_Days", default=30, cast=float))
TRUCK_CAP  = float(_get_setting(settings, "Vehicle_Capacity_tons", default=11.5, cast=float))
VEHICLES   = int(_get_setting(settings, "Vehicles_Total", default=30, cast=float))
MAX_TRIPS_PER_VEH_PER_DAY = int(_get_setting(settings, "Max_Trips_Per_Vehicle_Per_Day", default=1, cast=float))
MAX_TRIPS  = VEHICLES * MAX_TRIPS_PER_VEH_PER_DAY
DAILY_CAP  = MAX_TRIPS * TRUCK_CAP

# Pre-dispatch offset X for negative-day slider (based on CG->LG Day)
daily_total_cg = dispatch_cg.groupby("Day")["Quantity_tons"].sum()
cum_need = 0.0
adv = []
for d in range(1, int(DAYS) + 1):
    need = float(daily_total_cg.get(d, 0.0))
    cum_need += need
    over = (cum_need - DAILY_CAP * d) / (DAILY_CAP if DAILY_CAP else 1.0)
    adv.append(math.ceil(over) if over > 0 else 0)
X = max(adv) if adv else 0
MIN_DAY = int(1 - X)
MAX_DAY = int(DAYS)

# Aggregations aligned to your sheet names
day_totals_cg = (
    dispatch_cg.groupby("Day")["Quantity_tons"]
    .sum().reset_index()
)
day_totals_lg = (
    dispatch_lg.groupby("Day")["Quantity_tons"]
    .sum().reset_index()
)
veh_usage = (
    dispatch_lg.groupby("Day")["Vehicle_ID"]
    .nunique().reset_index(name="Trips_Used")
)
veh_usage["Max_Trips"] = MAX_TRIPS

# LG stock (pivot) from Stock_Levels
lg_stock = (
    stock_levels[stock_levels["Entity_Type"] == "LG"]
    .pivot(index="Day", columns="Entity_ID", values="Stock_Level_tons")
    .sort_index()
    .ffill()
)

# FPS thresholds — compute if absent
if "Reorder_Threshold_tons" not in fps.columns:
    fps = fps.copy()
    fps["Daily_Demand_tons"] = fps["Monthly_Demand_tons"] / 30.0
    fps["Lead_Time_days"] = fps["Lead_Time_days"].fillna(2)
    fps["Reorder_Threshold_tons"] = fps["Daily_Demand_tons"] * fps["Lead_Time_days"]

fps_stock = (
    stock_levels[stock_levels["Entity_Type"] == "FPS"]
    .merge(fps[["FPS_ID", "Reorder_Threshold_tons"]], left_on="Entity_ID", right_on="FPS_ID", how="left")
)
fps_stock["At_Risk"] = fps_stock["Stock_Level_tons"] <= fps_stock["Reorder_Threshold_tons"]

total_plan = float(day_totals_lg["Quantity_tons"].sum())

# ————————————————————————————————
# 5. Filters (Sidebar)
# ————————————————————————————————
with st.sidebar:
    st.header("Filters")
    day_range = st.slider(
        "Dispatch Window (days)",
        min_value=int(MIN_DAY), max_value=int(MAX_DAY),
        value=(int(MIN_DAY), int(MAX_DAY)),
        format="%d"
    )
    st.subheader("Select LGs")
    cols = st.columns(4)
    selected_lgs = []
    for i, lg in enumerate(lg_stock.columns):
        if co
