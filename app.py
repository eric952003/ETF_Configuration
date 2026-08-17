import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_gsheets import GSheetsConnection
import yfinance as yf

st.set_page_config(page_title="投資組合資產配置監控", layout="wide")

# 1. 目標比例與警示容許誤差設定
TARGET_ALLOCATION = {
    "市值型": 40.0,
    "高股息": 25.0,
    "全球型": 20.0,
    "主動型": 10.0,
    "債券": 5.0,
}
TOLERANCE_PCT = 3.0

# 建立 Google Sheets 連線物件
conn = st.connection("gsheets", type=GSheetsConnection)


def load_portfolio_data():
    """從 Google Sheet 讀取最新持股資料"""
    df = conn.read(ttl=0)
    if df is None or df.empty:
        return pd.DataFrame(columns=["代號", "名稱", "類別", "持股數", "幣別"])

    df = df.dropna(how="all")
    # 確保必要欄位存在
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
        if not rate or pd.isna(rate):
            rate = usd.history(period="5d")["Close"].iloc[-1]
        return float(rate)
    except Exception:
        return 32.0


@st.cache_data(ttl=300)
def fetch_price(symbol):
    """取得單一股票/ETF最新市價"""
    try:
        ticker = yf.Ticker(symbol)
        price = ticker.fast_info.get("last_price")
        if not price or pd.isna(price):
            hist = ticker.history(period="5d")
            price = hist["Close"].iloc[-1] if not hist.empty else 0.0
        return float(price)
    except Exception:
        return 0.0


# -------------------------------------------------------------
# 2. 介面呈現與編輯器
# -------------------------------------------------------------
st.title("📊 投資組合資產配置與雲端同步監控")
st.caption(
    "💡 支援台股 (.TW / .TWO) 與美股標的，資料直接與 Google 試算表即時雙向同步"
)

# 讀取 Google Sheets 現有資料
try:
    current_df = load_portfolio_data()
except Exception as e:
    st.error(f"⚠️ 連線至 Google 試算表失敗，請檢查 secrets 設定。錯誤原因: {e}")
    current_df = pd.DataFrame(
        columns=["代號", "名稱", "類別", "持股數", "幣別"]
    )

st.subheader("✏️ 持股編輯器")
st.info("可以在表格直接點擊修改、新增或刪除列，編輯完成後請點擊下方「💾 儲存修改至雲端」。")

edited_df = st.data_editor(
    current_df,
    num_rows="dynamic",
    column_config={
        "代號": st.column_config.TextColumn(
            "標的代號 (例如 0050.TW, VT)", required=True
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

# 儲存按鈕
col_btn1, col_btn2 = st.columns([1, 4])
with col_btn1:
    save_clicked = st.button("💾 儲存修改至雲端", type="primary")

if save_clicked:
    try:
        # 清除空白列並回寫 Google Sheet
        clean_df = edited_df.dropna(subset=["代號"])
        conn.update(data=clean_df)
        st.success("✅ 已成功同步保存至 Google 試算表！")
        st.rerun()
    except Exception as e:
        st.error(f"儲存失敗: {e}")

# -------------------------------------------------------------
# 3. 市值計算與再平衡邏輯
# -------------------------------------------------------------
st.divider()

valid_df = edited_df.dropna(subset=["代號"]).copy()

if not valid_df.empty:
    with st.spinner("正在爬取即時股價與匯率..."):
        usd_rate = get_usd_twd_rate()
        prices = []
        market_values = []

        for _, row in valid_df.iterrows():
            sym = str(row["代號"]).strip()
            shares = float(row["持股數"]) if pd.notna(row["持股數"]) else 0.0
            curr = str(row["幣別"]).strip()

            p = fetch_price(sym)
            prices.append(p)
            val = p * shares * (usd_rate if curr == "USD" else 1.0)
            market_values.append(val)

        calc_df = valid_df.copy()
        calc_df["即時單價"] = prices
        calc_df["市值(TWD)"] = market_values
        total_value = sum(market_values)

        # 頂部數據儀表板
        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("💰 投資組合總市值 (TWD)", f"{total_value:,.0f} 元")
        col_m2.metric("💵 USD/TWD 即時匯率", f"{usd_rate:.2f}")
        col_m3.metric("📌 持有標的總數", f"{len(valid_df)} 檔")

        # 統計各大類別
        summary_rows = []
        for cat, target_pct in TARGET_ALLOCATION.items():
            cat_val = calc_df[calc_df["類別"] == cat]["市值(TWD)"].sum()
            actual_pct = (
                (cat_val / total_value * 100) if total_value > 0 else 0.0
            )
            diff_pct = actual_pct - target_pct
            target_val = total_value * (target_pct / 100.0)
            rebalance_amt = target_val - cat_val

            if diff_pct > TOLERANCE_PCT:
                status = "⚠️ 超配 (Over)"
                sugg = f"比重偏高 **+{diff_pct:.1f}%**，建議暫停投入或調節賣出約 **{abs(rebalance_amt):,.0f}** 元"
            elif diff_pct < -TOLERANCE_PCT:
                status = "📉 欠配 (Under)"
                sugg = f"比重偏低 **{diff_pct:.1f}%**，建議定期定額或新增資金優先加碼約 **{rebalance_amt:,.0f}** 元"
            else:
                status = "✅ 正常 (Normal)"
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

        # 圓餅圖與類別清單
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

        # 再平衡警示面板
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
else:
    st.info("💡 目前 Google 試算表中尚無持股資料，請在上方表格新增股票並點擊儲存。")
