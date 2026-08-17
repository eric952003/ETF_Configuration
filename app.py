import pandas as pd
import plotly.express as px
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="投資組合資產配置監控", layout="wide")

# 1. 預設配置與目標比例
TARGET_ALLOCATION = {
    "市值型": 40.0,
    "高股息": 25.0,
    "全球型": 20.0,
    "主動型": 10.0,
    "債券": 5.0,
}
TOLERANCE_PCT = 3.0


@st.cache_data(ttl=300)
def get_usd_twd_rate():
    try:
        usd = yf.Ticker("TWD=X")
        rate = usd.fast_info.get("last_price")
        if not rate:
            rate = usd.history(period="5d")["Close"].iloc[-1]
        return float(rate)
    except Exception:
        return 32.0


@st.cache_data(ttl=300)
def fetch_price(symbol):
    try:
        ticker = yf.Ticker(symbol)
        price = ticker.fast_info.get("last_price")
        if not price or pd.isna(price):
            hist = ticker.history(period="5d")
            price = hist["Close"].iloc[-1] if not hist.empty else 0.0
        return float(price)
    except Exception:
        return 0.0


# 2. 標題與側邊欄設定
st.title("📊 投資組合資產配置與再平衡分析")
st.caption("支援台股 (.TW / .TWO) 與美股標的，自動即時抓取股價與匯率換算")

# 預設持股資料
default_holdings = pd.DataFrame(
    [
        {
            "代號": "0050.TW",
            "名稱": "元大台灣50",
            "類別": "市值型",
            "持股數": 2000,
            "幣別": "TWD",
        },
        {
            "代號": "006208.TW",
            "名稱": "富邦台50",
            "類別": "市值型",
            "持股數": 1000,
            "幣別": "TWD",
        },
        {
            "代號": "00878.TW",
            "名稱": "國泰永續高股息",
            "類別": "高股息",
            "持股數": 5000,
            "幣別": "TWD",
        },
        {
            "代號": "0056.TW",
            "名稱": "元大高股息",
            "類別": "高股息",
            "持股數": 2000,
            "幣別": "TWD",
        },
        {
            "代號": "VT",
            "名稱": "全世界股票ETF",
            "類別": "全球型",
            "持股數": 40,
            "幣別": "USD",
        },
        {
            "代號": "2330.TW",
            "name": "台積電",
            "類別": "主動型",
            "持股數": 100,
            "幣別": "TWD",
        },
        {
            "代號": "00679B.TW",
            "名稱": "元大美債20年",
            "類別": "債券",
            "持股數": 1000,
            "幣別": "TWD",
        },
    ]
)

# 3. 可編輯表格（直接在網頁上增刪改）
st.subheader("✏️ 持股編輯器")
edited_df = st.data_editor(
    default_holdings,
    num_rows="dynamic",
    column_config={
        "類別": st.column_config.SelectboxColumn(
            "資產類別", options=list(TARGET_ALLOCATION.keys()), required=True
        ),
        "幣別": st.column_config.SelectboxColumn(
            "幣別", options=["TWD", "USD"], required=True
        ),
        "持股數": st.column_config.NumberColumn(
            "持股數 (股)", min_value=0, step=100
        ),
    },
    use_container_width=True,
)

# 4. 計算資產總值與佔比
if st.button("🔄 立即重新計算與分析", type="primary"):
    with st.spinner("正在爬取即時股價與匯率..."):
        usd_rate = get_usd_twd_rate()

        prices = []
        market_values = []

        for _, row in edited_df.iterrows():
            sym = str(row["代號"]).strip()
            shares = float(row["持股數"])
            curr = str(row["幣別"]).strip()

            p = fetch_price(sym)
            prices.append(p)

            val = p * shares * (usd_rate if curr == "USD" else 1.0)
            market_values.append(val)

        edited_df["即時單價"] = prices
        edited_df["市值(TWD)"] = market_values

        total_value = sum(market_values)

        # 頂部數據看板
        col_m1, col_m2 = st.columns(2)
        col_m1.metric("💰 投資組合總市值 (TWD)", f"{total_value:,.0f} 元")
        col_m2.metric("💵 當前 USD/TWD 匯率", f"{usd_rate:.2f}")

        # 彙整各分類比例
        summary_rows = []
        for cat, target_pct in TARGET_ALLOCATION.items():
            cat_val = edited_df[edited_df["類別"] == cat]["市值(TWD)"].sum()
            actual_pct = (
                (cat_val / total_value * 100) if total_value > 0 else 0.0
            )
            diff_pct = actual_pct - target_pct
            target_val = total_value * (target_pct / 100.0)
            rebalance_amt = target_val - cat_val

            if diff_pct > TOLERANCE_PCT:
                status = "⚠️ 超配 (Over)"
                color = "inverse"
                sugg = f"比重偏高 **+{diff_pct:.1f}%**，建議暫停投入或調節賣出約 **{abs(rebalance_amt):,.0f}** 元"
            elif diff_pct < -TOLERANCE_PCT:
                status = "📉 欠配 (Under)"
                color = "normal"
                sugg = f"比重偏低 **{diff_pct:.1f}%**，定期定額優先加碼約 **{rebalance_amt:,.0f}** 元"
            else:
                status = "✅ 正常 (Normal)"
                color = "off"
                sugg = "比例符合目標配置，維持現有策略"

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

        # 5. 視覺化圓餅圖與持股明細
        st.divider()
        col_chart, col_data = st.columns([1, 1])

        with col_chart:
            st.subheader("🥧 實際資產佔比分佈")
            fig = px.pie(
                sum_df,
                values="市值",
                names="類別",
                hole=0.45,
                color_discrete_sequence=px.colors.qualitative.Set2,
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

        # 6. 再平衡建議與警示卡片
        st.divider()
        st.subheader("💡 再平衡與風險提示")

        for _, r in sum_df.iterrows():
            if "超配" in r["狀態"]:
                st.warning(
                    f"**【{r['類別']}】（目標 {r['目標佔比']:.0f}% / 實際 {r['實際佔比']:.1f}%）**：{r['建議']}"
                )
            elif "欠配" in r["狀態"]:
                st.info(
                    f"**【{r['類別']}】（目標 {r['目標佔比']:.0f}% / 實際 {r['實際佔比']:.1f}%）**：{r['建議']}"
                )
            else:
                st.success(
                    f"**【{r['類別']}】（目標 {r['目標佔比']:.0f}% / 實際 {r['實際佔比']:.1f}%）**：{r['建議']}"
                )