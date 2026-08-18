from datetime import datetime
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_gsheets import GSheetsConnection
import yfinance as yf

# -------------------------------------------------------------
# 1. 基本設定與常數定義
# -------------------------------------------------------------
st.set_page_config(page_title="投資組合資產配置監控", layout="wide")

TARGET_ALLOCATION = {
    "市值型": 40.0,
    "高股息": 25.0,
    "全球型": 20.0,
    "主動型": 10.0,
    "債券": 5.0,
}
TOLERANCE_PCT = 3.0

conn = st.connection("gsheets", type=GSheetsConnection)


# -------------------------------------------------------------
# 2. 資料讀取與 API 快取函數 (含強固防呆)
# -------------------------------------------------------------
def load_portfolio_data():
    """從 Google Sheet 讀取最新持股資料"""
    df = conn.read(ttl=0)
    if df is None or df.empty:
        return pd.DataFrame(columns=["代號", "名稱", "類別", "持股數", "幣別"])

    df = df.dropna(how="all")
    for col in ["代號", "名稱", "類別", "持股數", "幣別"]:
        if col not in df.columns:
            df[col] = ""
    return df


@st.cache_data(ttl=300)
def get_usd_twd_rate():
    """取得最新 USD/TWD 匯率"""
    try:
        usd = yf.Ticker("TWD=X")
        rate = usd.fast_info.get("last_price")
        if rate is None or pd.isna(rate):
            hist = usd.history(period="5d")
            rate = (
                hist["Close"].dropna().iloc[-1]
                if (not hist.empty and "Close" in hist.columns)
                else 32.0
            )
        return float(rate)
    except Exception:
        return 32.0


@st.cache_data(ttl=300)
def fetch_stock_info(symbol):
    """取得單一股票/ETF最新市價與當月除息金額 (完整防呆版)"""
    price = 0.0
    current_month_div = 0.0

    if not symbol or pd.isna(symbol):
        return 0.0, 0.0

    symbol = str(symbol).strip()
    if not symbol:
        return 0.0, 0.0

    try:
        ticker = yf.Ticker(symbol)

        # 1. 取得市價
        try:
            p = ticker.fast_info.get("last_price")
            if p is not None and not pd.isna(p):
                price = float(p)
            else:
                hist = ticker.history(period="5d")
                if not hist.empty and "Close" in hist.columns:
                    valid_closes = hist["Close"].dropna()
                    if not valid_closes.empty:
                        price = float(valid_closes.iloc[-1])
        except Exception:
            price = 0.0

        # 2. 取得當月除權息金額
        try:
            divs = ticker.dividends
            if (
                divs is not None
                and hasattr(divs, "empty")
                and not divs.empty
                and hasattr(divs, "index")
            ):
                div_dates = pd.to_datetime(divs.index).tz_localize(None)
                now = datetime.now()
                mask = (div_dates.year == now.year) & (
                    div_dates.month == now.month
                )
                m_divs = divs[mask]
                if not m_divs.empty:
                    val = m_divs.sum()
                    if val is not None and pd.notna(val):
                        current_month_div = float(val)
        except Exception:
            current_month_div = 0.0

    except Exception:
        pass

    return float(price), float(current_month_div)


# -------------------------------------------------------------
# 3. 網頁標題與持股編輯器
# -------------------------------------------------------------
st.title("📊 投資組合資產配置與現金流監控")
st.caption(
    "💡 支援台美股即時報價、當月除息可用資金計算與 Google Sheet 雙向同步"
)

try:
    current_df = load_portfolio_data()
except Exception as e:
    st.error(f"⚠️ 連線至 Google 試算表失敗，請檢查 secrets 設定。錯誤原因: {e}")
    current_df = pd.DataFrame(
        columns=["代號", "名稱", "類別", "持股數", "幣別"]
    )

st.subheader("✏️ 持股編輯器")
edited_df = st.data_editor(
    current_df,
    num_rows="dynamic",
    column_config={
        "代號": st.column_config.TextColumn(
            "標的代號 (例如 0050.TW, 00878.TW, VT)", required=True
        ),
        "名稱": st.column_config.TextColumn("標的名稱", required=True),
        "類別": st.column_config.SelectboxColumn(
            "資產類別", options=list(TARGET_ALLOCATION.keys()), required=True
        ),
        "幣別": st.column_config.SelectboxColumn(
            "計價幣別", options=["TWD", "USD"], required=True
        ),
        "持股數": st.column_config.NumberColumn(
            "持股數 (股)", min_value=0.0, step=100.0, required=True
        ),
    },
    use_container_width=True,
)

if st.button("💾 儲存修改至雲端", type="primary"):
    try:
        clean_df = edited_df.dropna(subset=["代號"])
        conn.update(data=clean_df)
        st.success("✅ 已成功同步保存至 Google 試算表！")
        st.rerun()
    except Exception as e:
        st.error(f"儲存失敗: {e}")

# -------------------------------------------------------------
# 4. 市值計算與當月除息現金流統計
# -------------------------------------------------------------
st.divider()
valid_df = edited_df.dropna(subset=["代號"]).copy()

if not valid_df.empty:
    with st.spinner("正在爬取即時股價、當月配息與匯率..."):
        usd_rate = get_usd_twd_rate()
        now = datetime.now()
        prices = []
        market_values = []
        div_per_share_list = []
        total_div_twd_list = []

        for _, row in valid_df.iterrows():
            sym = str(row["代號"]).strip()
            shares = float(row["持股數"]) if pd.notna(row["持股數"]) else 0.0
            curr = str(row["幣別"]).strip()

            p, d = fetch_stock_info(sym)
            rate_factor = usd_rate if curr == "USD" else 1.0

            val = p * shares * rate_factor
            div_val = d * shares * rate_factor

            prices.append(p)
            market_values.append(val)
            div_per_share_list.append(d)
            total_div_twd_list.append(div_val)

        calc_df = valid_df.copy()
        calc_df["即時單價"] = prices
        calc_df["市值(TWD)"] = market_values
        calc_df["當月單股配息"] = div_per_share_list
        calc_df["當月預估配息(TWD)"] = total_div_twd_list

        total_value = sum(market_values)
        total_monthly_dividends = sum(total_div_twd_list)

        # 頂部儀表板
        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        col_m1.metric("💰 投資組合總市值", f"{total_value:,.0f} 元")
        col_m2.metric(
            f"📅 {now.month} 月除息預估股息",
            f"{total_monthly_dividends:,.0f} 元",
        )
        col_m3.metric("💵 USD/TWD 匯率", f"{usd_rate:.2f}")
        col_m4.metric("📌 持有標的數", f"{len(valid_df)} 檔")

        # -------------------------------------------------------------
        # 5. 當月可用資金規劃
        # -------------------------------------------------------------
        st.subheader("💵 當月可用投資資金規劃")
        col_inp, col_res = st.columns([1, 1])

        with col_inp:
            extra_budget = st.number_input(
                "➕ 本月預計新增投入資金 (定期定額/薪資投入, TWD)",
                min_value=0,
                value=0,
                step=1000,
            )
            total_investable = total_monthly_dividends + extra_budget

        with col_res:
            st.metric(
                label=f"🎯 {now.month} 月總可用投資金額 (股息 + 新增資金)",
                value=f"{total_investable:,.0f} 元",
                help="當月已除息或預估發放的股息現金，加上額外新增預算，可用於再平衡補進欠配資產",
            )

        # -------------------------------------------------------------
        # 6. 資產配置與再平衡建議
        # -------------------------------------------------------------
        summary_rows = []
        under_allocated_cats = []

        for cat, target_pct in TARGET_ALLOCATION.items():
            cat_val = calc_df[calc_df["類別"] == cat]["市值(TWD)"].sum()
            actual_pct = (
                (cat_val / total_value * 100) if total_value > 0 else 0.0
            )
            diff_pct = actual_pct - target_pct
            target_val = total_value * (target_pct / 100.0)
            rebalance_amt = target_val - cat_val

            if diff_pct < -TOLERANCE_PCT:
                status = "📉 欠配 (Under)"
                under_allocated_cats.append((cat, rebalance_amt))
                sugg = f"比重偏低 **{diff_pct:.1f}%**，建議加碼約 **{rebalance_amt:,.0f}** 元"
            elif diff_pct > TOLERANCE_PCT:
                status = "⚠️ 超配 (Over)"
                sugg = f"比重偏高 **+{diff_pct:.1f}%**，建議暫停投入約 **{abs(rebalance_amt):,.0f}** 元"
            else:
                status = "✅ 正常 (Normal)"
                sugg = "比例符合目標配置，維持現有步調"

            summary_rows.append(
                {
                    "類別": cat,
                    "市值": cat_val,
                    "實際佔比": actual_pct,
                    "目標佔比": target_pct,
                    "偏離度": diff_pct,
                    "狀態": status,
                    "建議": sugg,
                }
            )

        sum_df = pd.DataFrame(summary_rows)

        col_chart, col_data = st.columns([1, 1])
        with col_chart:
            st.subheader("🥧 實際資產佔比分佈")
            fig = px.pie(
                sum_df,
                values="市值",
                names="類別",
                hole=0.45,
                color_discrete_sequence=px.colors.qualitative.Pastel,
            )
            fig.update_traces(textposition="inside", textinfo="percent+label")
            st.plotly_chart(fig, use_container_width=True)

        with col_data:
            st.subheader("📋 各類別配置統計")
            st.dataframe(
                sum_df[
                    ["類別", "市值", "實際佔比", "目標佔比", "狀態"]
                ].style.format(
                    {
                        "市值": "{:,.0f} 元",
                        "實際佔比": "{:.2f}%",
                        "目標佔比": "{:.1f}%",
                    }
                ),
                use_container_width=True,
                height=250,
            )

        # 資金分配指引
        st.subheader("💡 當月可用資金再平衡配置建議")
        if total_investable > 0:
            if under_allocated_cats:
                total_under_need = sum([amt for _, amt in under_allocated_cats])
                st.write(
                    f"您本月共有 **{total_investable:,.0f}** 元可用於再平衡，建議依欠配比例分配至以下類別："
                )
                for cat, need_amt in under_allocated_cats:
                    ratio = (
                        (need_amt / total_under_need)
                        if total_under_need > 0
                        else (1 / len(under_allocated_cats))
                    )
                    allocate_for_cat = total_investable * ratio
                    st.success(
                        f"👉 **【{cat}】**：建議分配 **{allocate_for_cat:,.0f}** 元（目標需補足約 {need_amt:,.0f} 元）"
                    )
            else:
                st.success(
                    "🎉 目前所有資產類別皆在合理平衡區間內！本月可用資金可依目標比例等比例投入。"
                )
        else:
            st.info("💡 若當月無除權息或尚未設定新增資金，可用金額為 0 元。")
else:
    st.info("💡 目前尚無持股資料，請在上方表格輸入標的並點擊儲存。")
