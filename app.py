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
# 2. Helper to export DataFrame to Excel
# ————————————————————————————————
def to_excel(df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()

# ————————————————————————————————
# 3. Load & Cache Data (from uploaded bytes only)
# ————————————————————————————————
@st.cache_data(show_spinner=False)
def load_data_from_bytes(b: bytes):
    with BytesIO(b) as bio:
        xl = pd.ExcelFile(bio)
        need = ["Settings", "CG_to_LG_Dispatch", "LG_to_FPS_Dispatch", "Stock_Levels", "LGs", "FPS"]
        missing = [s for s in need if s not in xl.sheet_names]
        if missing:
            raise ValueError(f"Missing sheets: {missing}")

        settings     = xl.parse("Settings")
        dispatch_cg  = xl.parse("CG_to_LG_Dispatch")
        dispatch_lg  = xl.parse("LG_to_FPS_Dispatch")
        stock_levels = xl.parse("Stock_Levels")
        lgs          = xl.parse("LGs")
        fps          = xl.parse("FPS")
        return settings, dispatch_cg, dispatch_lg, stock_levels, lgs, fps

# ————————————————————————————————
# 3A. File Uploader (REQUIRED)
# ————————————————————————————————
st.title("🚛 Grain Distribution Dashboard")

with st.sidebar:
    st.header("Input File (required)")
    uploaded = st.file_uploader("Upload Excel file (.xlsx) with required sheets", type=["xlsx"])

if uploaded is None:
    st.info("Please upload an Excel file to view the dashboard.")
    st.stop()

# read uploaded bytes (safe for multiple reruns)
try:
    settings, dispatch_cg, dispatch_lg, stock_levels, lgs, fps = load_data_from_bytes(uploaded.getvalue())
    st.success(f"Loaded data from: {uploaded.name}")
except Exception as e:
    st.error(f"Failed to load data: {e}")
    st.stop()

# ————————————————————————————————
# 4. Compute Core Metrics
# ————————————————————————————————
DAYS      = int(settings.query("Parameter=='Distribution_Days'")["Value"].iloc[0])
TRUCK_CAP = float(settings.query("Parameter=='Vehicle_Capacity_tons'")["Value"].iloc[0])
MAX_TRIPS = int(settings.query("Parameter=='Vehicles_Total'")["Value"].iloc[0]) * \
            int(settings.query("Parameter=='Max_Trips_Per_Vehicle_Per_Day'")["Value"].iloc[0])
DAILY_CAP = MAX_TRIPS * TRUCK_CAP

# Pre-dispatch offset X for negative-day slider
daily_total_cg = dispatch_cg.groupby("Dispatch_Day")["Quantity_tons"].sum()
cum_need = 0
adv = []
for d in range(1, DAYS+1):
    need = daily_total_cg.get(d, 0)
    cum_need += need
    over = (cum_need - DAILY_CAP * d) / DAILY_CAP
    adv.append(math.ceil(over) if over > 0 else 0)
X = max(adv) if adv else 0
MIN_DAY = 1 - X
MAX_DAY = DAYS

# Aggregations
day_totals_cg = (
    dispatch_cg.groupby("Dispatch_Day")["Quantity_tons"]
    .sum().reset_index().rename(columns={"Dispatch_Day":"Day"})
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

lg_stock = (
    stock_levels[stock_levels.Entity_Type=="LG"]
    .pivot(index="Day", columns="Entity_ID", values="Stock_Level_tons")
    .fillna(method="ffill")
)

fps_stock = (
    stock_levels[stock_levels.Entity_Type=="FPS"]
    .merge(fps[["FPS_ID","Reorder_Threshold_tons"]], left_on="Entity_ID", right_on="FPS_ID")
)
fps_stock["At_Risk"] = fps_stock.Stock_Level_tons <= fps_stock.Reorder_Threshold_tons

total_plan = float(day_totals_lg.Quantity_tons.sum())

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
        if cols[i % 4].checkbox(str(lg), value=True, key=f"lg_{lg}"):
            selected_lgs.append(lg)
    st.markdown("---")
    st.header("Quick KPIs")
    cg_sel = day_totals_cg.query("Day>=@day_range[0] & Day<=@day_range[1]")["Quantity_tons"].sum()
    lg_sel = day_totals_lg.query("Day>=1 & Day<=@day_range[1]")["Quantity_tons"].sum()
    st.metric("CG→LG Total (t)", f"{cg_sel:,.1f}")
    st.metric("LG→FPS Total (t)", f"{lg_sel:,.1f}")
    st.metric("Max Trucks/Day", int(MAX_TRIPS))
    st.metric("Truck Capacity (t)", f"{TRUCK_CAP}")

# ————————————————————————————————
# 5A. Precompute FPS report once (used in multiple tabs)
# ————————————————————————————————
end_day = min(day_range[1], DAYS)
fps_df_filtered = dispatch_lg.query("Day>=1 & Day<=@day_range[1]")
report = (
    fps_df_filtered.groupby("FPS_ID")
    .agg(
        Total_Dispatched_tons=pd.NamedAgg("Quantity_tons","sum"),
        Trips_Count=pd.NamedAgg("Vehicle_ID","count"),
        Vehicle_IDs=pd.NamedAgg("Vehicle_ID", lambda vs: ",".join(map(str,sorted(set(vs)))))
    )
    .reset_index()
    .merge(fps[["FPS_ID","FPS_Name"]], on="FPS_ID", how="left")
    .sort_values("Total_Dispatched_tons", ascending=False)
)

# Create tabs
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "CG→LG Overview", "LG→FPS Overview", 
    "FPS Report", "FPS At-Risk", 
    "FPS Data", "Downloads", "Metrics"
])

# ————————————————————————————————
# 6. CG→LG Overview
# ————————————————————————————————
with tab1:
    st.subheader("CG → LG Dispatch")
    df1 = day_totals_cg.query("Day>=@day_range[0] & Day<=@day_range[1]")
    fig1 = px.bar(df1, x="Day", y="Quantity_tons", text="Quantity_tons")
    fig1.update_traces(texttemplate="%{text:.1f}t", textposition="outside")
    st.plotly_chart(fig1, use_container_width=True)

# ————————————————————————————————
# 7. LG→FPS Overview
# ————————————————————————————————
with tab2:
    st.subheader("LG → FPS Dispatch")
    df2 = day_totals_lg.query("Day>=1 & Day<=@day_range[1]")
    fig2 = px.bar(df2, x="Day", y="Quantity_tons", text="Quantity_tons")
    fig2.update_traces(texttemplate="%{text:.1f}t", textposition="outside")
    st.plotly_chart(fig2, use_container_width=True)

# ————————————————————————————————
# 8. FPS Report
# ————————————————————————————————
with tab3:
    st.subheader("FPS-wise Dispatch Details")
    st.dataframe(report, use_container_width=True)

# ————————————————————————————————
# 9. FPS At-Risk
# ————————————————————————————————
with tab4:
    st.subheader("FPS At-Risk List")
    arf = fps_stock.query("Day>=1 & Day<=@day_range[1] & At_Risk")[[
        "Day","FPS_ID","Stock_Level_tons","Reorder_Threshold_tons"
    ]]
    st.dataframe(arf, use_container_width=True)
    st.download_button(
        "Download At-Risk (Excel)",
        to_excel(arf),
        "fps_at_risk.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ————————————————————————————————
# 10. FPS Data
# ————————————————————————————————
with tab5:
    st.subheader("FPS Stock & Upcoming Receipts")
    fps_data = []
    fps_indexed = fps.set_index("FPS_ID")
    for fps_id in fps.FPS_ID:
        s = fps_stock[(fps_stock.FPS_ID==fps_id) & (fps_stock.Day==end_day)]["Stock_Level_tons"]
        stock_now = float(s.iloc[0]) if not s.empty else 0.0
        future = dispatch_lg[(dispatch_lg.FPS_ID==fps_id) & (dispatch_lg.Day> end_day)]["Day"]
        next_day = int(future.min()) if not future.empty else None
        days_to = (next_day - end_day) if next_day else None
        fps_name = fps_indexed.loc[fps_id,"FPS_Name"] if fps_id in fps_indexed.index else None
        fps_data.append({
            "FPS_ID": fps_id,
            "FPS_Name": fps_name,
            "Current_Stock_tons": stock_now,
            "Next_Receipt_Day": next_day,
            "Days_To_Receipt": days_to
        })
    fps_data_df = pd.DataFrame(fps_data)
    st.dataframe(fps_data_df, use_container_width=True)
    st.download_button(
        "Download FPS Data (Excel)",
        to_excel(fps_data_df),
        "fps_data.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ————————————————————————————————
# 11. Downloads
# ————————————————————————————————
with tab6:
    st.subheader("Download FPS Report")
    st.download_button(
        "Excel",
        to_excel(report),
        f"FPS_Report_{max(day_range[0],1)}_to_{day_range[1]}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    pdf_buf = BytesIO()
    with PdfPages(pdf_buf) as pdf:
        fig, ax = plt.subplots(figsize=(8, max(1, len(report)*0.3) + 1))
        ax.axis('off')
        tbl = ax.table(cellText=report.values, colLabels=report.columns, loc='center')
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(10)
        pdf.savefig(fig, bbox_inches='tight')
    st.download_button(
        "PDF",
        pdf_buf.getvalue(),
        f"FPS_Report_{max(day_range[0],1)}_to_{day_range[1]}.pdf",
        mime="application/pdf"
    )

# ————————————————————————————————
# 12. Metrics
# ————————————————————————————————
with tab7:
    st.subheader("Key Performance Indicators")
    sel_days = day_range[1] - max(day_range[0],1) + 1
    avg_daily_cg = (day_totals_cg.query("Day>=@day_range[0] & Day<=@day_range[1]")["Quantity_tons"].sum()/sel_days) if sel_days>0 else 0
    avg_daily_lg = (day_totals_lg.query("Day>=1 & Day<=@day_range[1]")["Quantity_tons"].sum()/sel_days) if sel_days>0 else 0
    avg_trips    = veh_usage.query("Day>=1 & Day<=@day_range[1]")["Trips_Used"].mean()
    pct_fleet    = (avg_trips / MAX_TRIPS)*100 if MAX_TRIPS else 0

    lg_onhand    = lg_stock.loc[end_day, selected_lgs].sum() if len(selected_lgs)>0 and end_day in lg_stock.index else 0.0
    fps_onhand   = fps_stock.query("Day==@end_day")["Stock_Level_tons"].sum()
    lg_caps      = lgs.set_index("LG_ID").loc[selected_lgs,"Storage_Capacity_tons"].sum() if len(selected_lgs)>0 else 0.0
    pct_lg_filled= (lg_onhand/lg_caps)*100 if lg_caps else 0
    fps_zero     = fps_stock.query("Day==@end_day & Stock_Level_tons==0")["FPS_ID"].nunique()
    fps_risk     = fps_stock.query("Day==@end_day & At_Risk")["FPS_ID"].nunique()
    dispatched_cum = day_totals_lg.query("Day<=@end_day")["Quantity_tons"].sum()
    pct_plan      = (dispatched_cum/total_plan)*100 if total_plan else 0
    remaining_t   = total_plan - dispatched_cum
    days_rem      = math.ceil(remaining_t/DAILY_CAP) if DAILY_CAP else None

    metrics = [
        ("Total CG→LG (t)",       f"{day_totals_cg.query('Day>=@day_range[0] & Day<=@day_range[1]')['Quantity_tons'].sum():,.1f}"),
        ("Total LG→FPS (t)",      f"{day_totals_lg.query('Day>=1 & Day<=@day_range[1]')['Quantity_tons'].sum():,.1f}"),
        ("Avg Daily CG→LG (t/d)", f"{avg_daily_cg:,.1f}"),
        ("Avg Daily LG→FPS (t/d)",f"{avg_daily_lg:,.1f}"),
        ("Avg Trips/Day",         f"{avg_trips:.1f}"),
        ("% Fleet Utilization",   f"{pct_fleet:.1f}%"),
        ("LG Stock on Hand (t)",  f"{lg_onhand:,.1f}"),
        ("FPS Stock on Hand (t)", f"{fps_onhand:,.1f}"),
        ("% LG Cap Filled",       f"{pct_lg_filled:.1f}%"),
        ("FPS Stock-Outs",        f"{fps_zero}"),
        ("FPS At-Risk Count",     f"{fps_risk}"),
        ("% Plan Completed",      f"{pct_plan:.1f}%"),
        ("Days Remaining",        f"{days_rem}")
    ]
    cols = st.columns(3)
    for i, (label, val) in enumerate(metrics):
        cols[i%3].metric(label, val)
