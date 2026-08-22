from datetime import datetime
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_gsheets import GSheetsConnection
import yfinance as yf

# -------------------------------------------------------------
# 1. 基本頁面設定
# -------------------------------------------------------------
st.set_page_config(page_title="投資組合資產配置監控", layout="wide")

conn = st.connection("gsheets", type=GSheetsConnection)


# -------------------------------------------------------------
# 2. 資料讀取與 API 快取函數
# -------------------------------------------------------------
def load_portfolio_data():
    """從 Google Sheet 讀取最新持股資料"""
    df = conn.read(ttl=0)
    expected_cols = ["代號", "名稱", "類別", "持股數", "平均成本", "幣別"]
    if df is None or df.empty:
        return pd.DataFrame(columns=expected_cols)

    df = df.dropna(how="all")
    for col in expected_cols:
        if col not in df.columns:
            df[col] = 0.0 if col in ["持股數", "平均成本"] else ""
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
    """取得最新市價與預估每月每股平均配息 (TTM 近12個月累計 / 12)"""
    price = 0.0
    monthly_est_div = 0.0

    if not symbol or pd.isna(symbol):
        return 0.0, 0.0

    symbol = str(symbol).strip()
    if not symbol:
        return 0.0, 0.0

    try:
        ticker = yf.Ticker(symbol)

        # 1. 取得即時市價
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

        # 2. 取得近 12 個月配息並換算月均現金流
        try:
            divs = ticker.dividends
            if (
                divs is not None
                and hasattr(divs, "empty")
                and not divs.empty
                and hasattr(divs, "index")
            ):
                div_dates = pd.to_datetime(divs.index).tz_localize(None)
                one_year_ago = pd.Timestamp.now() - pd.Timedelta(days=365)
                recent_divs = divs[div_dates >= one_year_ago]

                if not recent_divs.empty:
                    total_year_div = float(recent_divs.sum())
                    monthly_est_div = total_year_div / 12.0
                else:
                    last_val = float(divs.dropna().iloc[-1])
                    monthly_est_div = last_val / 12.0
        except Exception:
            monthly_est_div = 0.0

    except Exception:
        pass

    return float(price), float(monthly_est_div)


# -------------------------------------------------------------
# 3. 側邊欄：目標資產配置比例調整器
# -------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ 目標配置比例設定")
    st.caption("請在此自訂各資產類別的目標權重（合計需為 100%）")

    target_market = st.number_input(
        "📈 市值型 (%)",
        min_value=0.0,
        max_value=100.0,
        value=40.0,
        step=5.0,
        key="target_market",
    )
    target_dividend = st.number_input(
        "💰 高股息 (%)",
        min_value=0.0,
        max_value=100.0,
        value=25.0,
        step=5.0,
        key="target_dividend",
    )
    target_global = st.number_input(
        "🌍 全球型 (%)",
        min_value=0.0,
        max_value=100.0,
        value=20.0,
        step=5.0,
        key="target_global",
    )
    target_active = st.number_input(
        "🚀 主動型 (%)",
        min_value=0.0,
        max_value=100.0,
        value=10.0,
        step=5.0,
        key="target_active",
    )
    target_bond = st.number_input(
        "🛡️ 債券 (%)",
        min_value=0.0,
        max_value=100.0,
        value=5.0,
        step=5.0,
        key="target_bond",
    )

    total_target = (
        target_market
        + target_dividend
        + target_global
        + target_active
        + target_bond
    )

    if total_target == 100.0:
        st.success(f"✅ 目標比例總和：{total_target:.0f}%")
    else:
        st.error(
            f"⚠️ 目標比例總和為 **{total_target:.1f}%**，請調整至剛好 100%"
        )

    tolerance_pct = st.slider(
        "🎯 警示容許誤差 (±%)",
        min_value=1.0,
        max_value=10.0,
        value=3.0,
        step=0.5,
    )

TARGET_ALLOCATION = {
    "市值型": target_market,
    "高股息": target_dividend,
    "全球型": target_global,
    "主動型": target_active,
    "債券": target_bond,
}

# -------------------------------------------------------------
# 4. 主畫面：持股編輯器
# -------------------------------------------------------------
st.title("📊 投資組合資產配置、損益與現金流監控")
st.caption(
    "💡 支援即時股價、平均成本損益計算、自訂目標配置比例與 Google Sheet 雙向同步"
)

try:
    current_df = load_portfolio_data()
except Exception as e:
    st.error(f"⚠️ 連線至 Google 試算表失敗，請檢查 secrets 設定。錯誤原因: {e}")
    current_df = pd.DataFrame(
        columns=["代號", "名稱", "類別", "持股數", "平均成本", "幣別"]
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
        "持股數": st.column_config.NumberColumn(
            "持股數 (股)", min_value=0.0, step=100.0, required=True
        ),
        "平均成本": st.column_config.NumberColumn(
            "平均買進成本 (原幣別)", min_value=0.0, step=0.1, required=False
        ),
        "幣別": st.column_config.SelectboxColumn(
            "計價幣別", options=["TWD", "USD"], required=True
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
# 5. 市值、損益與每月股息計算
# -------------------------------------------------------------
st.divider()
valid_df = edited_df.dropna(subset=["代號"]).copy()

if not valid_df.empty:
    with st.spinner("正在爬取即時股價、計算成本損益與匯率..."):
        usd_rate = get_usd_twd_rate()
        prices = []
        cost_totals = []
        market_values = []
        pnl_twd_list = []
        roi_pct_list = []
        monthly_div_per_share_list = []
        monthly_div_twd_list = []

        for _, row in valid_df.iterrows():
            sym = str(row["代號"]).strip()
            shares = float(row["持股數"]) if pd.notna(row["持股數"]) else 0.0
            avg_cost = (
                float(row["平均成本"]) if pd.notna(row["平均成本"]) else 0.0
            )
            curr = str(row["幣別"]).strip()

            p, d_month = fetch_stock_info(sym)
            rate_factor = usd_rate if curr == "USD" else 1.0

            total_cost_twd = avg_cost * shares * rate_factor
            val = p * shares * rate_factor
            pnl_twd = val - total_cost_twd if avg_cost > 0 else 0.0
            roi_pct = (
                ((p - avg_cost) / avg_cost * 100.0)
                if (avg_cost > 0 and p > 0)
                else 0.0
            )
            div_val = d_month * shares * rate_factor

            prices.append(p)
            cost_totals.append(total_cost_twd)
            market_values.append(val)
            pnl_twd_list.append(pnl_twd)
            roi_pct_list.append(roi_pct)
            monthly_div_per_share_list.append(d_month)
            monthly_div_twd_list.append(div_val)

        calc_df = valid_df.copy()
        calc_df["即時單價"] = prices
        calc_df["總成本(TWD)"] = cost_totals
        calc_df["市值(TWD)"] = market_values
        calc_df["未實現損益(TWD)"] = pnl_twd_list
        calc_df["報酬率(%)"] = roi_pct_list
        calc_df["預估月均每股配息"] = monthly_div_per_share_list
        calc_df["預估每月配息(TWD)"] = monthly_div_twd_list

        total_value = sum(market_values)
        total_cost = sum(
            [
                c
                for c, avg in zip(cost_totals, calc_df["平均成本"])
                if float(avg) > 0
            ]
        )
        total_pnl = sum(pnl_twd_list)
        total_roi = (
            (total_pnl / total_cost * 100.0) if total_cost > 0 else 0.0
        )
        total_monthly_dividends = sum(monthly_div_twd_list)

        # 頂部儀表板
        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        col_m1.metric("💰 投資組合總市值", f"{total_value:,.0f} 元")

        pnl_delta_str = f"{total_pnl:+,.0f} 元 ({total_roi:+.2f}%)"
        col_m2.metric(
            label="📈 總未實現損益",
            value=f"{total_pnl:,.0f} 元",
            delta=f"{total_roi:+.2f}%",
        )

        col_m3.metric(
            "📅 預估每月平均股息",
            f"{total_monthly_dividends:,.0f} 元",
            help="基於各標的過去 12 個月歷史配息紀錄所折算之平均每月被動現金流",
        )
        col_m4.metric("💵 USD/TWD 匯率", f"{usd_rate:.2f}")

        # -------------------------------------------------------------
        # 6. 個股損益明細表
        # -------------------------------------------------------------
        st.subheader("📑 各標的損益與即時行情明細")

        display_df = calc_df[
            [
                "代號",
                "名稱",
                "類別",
                "持股數",
                "平均成本",
                "即時單價",
                "市值(TWD)",
                "未實現損益(TWD)",
                "報酬率(%)",
            ]
        ].copy()

        st.dataframe(
            display_df.style.format(
                {
                    "平均成本": "{:,.2f}",
                    "即時單價": "{:,.2f}",
                    "市值(TWD)": "{:,.0f} 元",
                    "未實現損益(TWD)": "{:+,.0f} 元",
                    "報酬率(%)": "{:+.2f}%",
                    "持股數": "{:,.0f}",
                }
            ),
            use_container_width=True,
        )

        # -------------------------------------------------------------
        # 7. 當月可用資金規劃
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
                label="🎯 本月總可用投資金額 (月均股息 + 新增資金)",
                value=f"{total_investable:,.0f} 元",
                help="預估每月平均股息現金流，加上額外新增預算，可用於再平衡補進欠配資產",
            )

        # -------------------------------------------------------------
        # 8. 目標 vs 實際配置分析與再平衡建議
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

            if diff_pct < -tolerance_pct:
                status = "📉 欠配 (Under)"
                under_allocated_cats.append((cat, rebalance_amt))
                sugg = f"比重偏低 **{diff_pct:.1f}%**，建議加碼約 **{rebalance_amt:,.0f}** 元"
            elif diff_pct > tolerance_pct:
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
        if total_target != 100.0:
            st.warning("⚠️ 請先在左側邊欄將各資產類別比例調整至合計 100%。")
        elif total_investable > 0:
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
            st.info("💡 若目前無配息現金流或尚未設定新增資金，可用金額為 0 元。")
else:
    st.info("💡 目前尚無持股資料，請在上方表格輸入標的並點擊儲存。")
