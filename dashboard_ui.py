"""dashboard_ui.py (v3)

このモジュールは、ダッシュボードのUIコンポーネント（グラフ、テーブル、タブなど）の
描画を担当します。データそのものは引数として受け取り、そのデータを元に
視覚的な要素を生成することに特化しています。
"""
import streamlit as st
import polars as pl
import plotly.graph_objs as go
import math
import pytz
import pandas as pd
from pathlib import Path
import json
from core import cfg
from datetime import datetime, timezone

def display_health_status(health_data: dict):
    """【タスク4追加】ヘッダーにシステムヘルスを表示する"""
    oanda_status = f"🟢 正常 ({health_data.get('oanda_latency_ms', 0)}ms)" if health_data.get("oanda") else "🔴 異常"
    redis_status = "🟢 正常" if health_data.get("redis") else "🔴 異常"
    
    st.markdown(f"**システム稼働状況** | OANDA API: {oanda_status} | Redis: {redis_status}")
    st.divider()

def parse_cron_to_ui(cron_str: str) -> tuple:
    """cron文字列(例: '30 6 * * 6')をUI用の(分, 時, 曜日)に分解する"""
    default_min, default_hour, default_dow = 0, 0, "*"
    if not cron_str:
        return default_min, default_hour, default_dow
    parts = cron_str.strip().split()
    if len(parts) >= 5:
        m, h, d, mon, dow = parts[:5]
        m_val = int(m) if m.isdigit() else 0
        h_val = int(h) if h.isdigit() else 0
        return m_val, h_val, dow
    return default_min, default_hour, default_dow

def build_cron_from_ui(m_val: int, h_val: int, dow_str: str) -> str:
    """UIの入力値からcron文字列を構築する"""
    return f"{m_val} {h_val} * * {dow_str}"

def plot_equity_curve(df: pl.DataFrame, pair: str, data_source: str) -> go.Figure:
    """資産曲線のグラフを生成する"""
    fig = go.Figure()
    initial_balance = cfg.get_sync("trading.initial_balance", 1_000_000)
    time_col = "Entry Time" if data_source == "backtest" else "ts"
    profit_col = "PNL" if data_source == "backtest" else "pnl"
    if df.is_empty() or time_col not in df.columns or profit_col not in df.columns: return fig
    equity = df[profit_col].cum_sum() + initial_balance
    fig.add_trace(go.Scatter(x=df[time_col].to_pandas(), y=equity.to_pandas(), mode="lines", name=f"{pair} 資産", line=dict(color="#00FF00")))
    fig.update_layout(title=f"{pair} 資産曲線", template="plotly_dark", xaxis=dict(rangeslider=dict(visible=True)))
    return fig

def plot_latency(df: pl.DataFrame) -> go.Figure:
    """関数別レイテンシーのグラフを生成する"""
    fig = go.Figure()
    if df.is_empty() or "func_name" not in df.columns: return fig
    for func_name in df["func_name"].unique():
        df_func = df.filter(pl.col("func_name") == func_name)
        for is_async in [True, False]:
            df_mode = df_func.filter(pl.col("is_async") == is_async)
            if not df_mode.is_empty():
                fig.add_trace(go.Scatter(x=df_mode["timestamp"].to_pandas(), y=df_mode["avg_time_ms"].to_pandas(), mode="lines+markers", name=f"{func_name} ({'Async' if is_async else 'Sync'})"))
    fig.update_layout(title="関数別レイテンシー", yaxis_title="平均実行時間 (ms)", template="plotly_dark")
    return fig

def plot_m5_atr(times: list, atr_values: list, threshold: float) -> go.Figure:
    """M5 ATRボラティリティのグラフを生成する"""
    fig = go.Figure()
    if not times or not atr_values: return fig
    fig.add_trace(go.Scatter(x=times, y=atr_values, mode="lines", name="M5 ATR", line=dict(color="blue")))
    fig.add_trace(go.Scatter(x=times, y=[threshold] * len(times), mode="lines", name="ボラティリティ閾値", line=dict(color="red", dash="dash")))
    fig.update_layout(title="M5 ATR ボラティリティ", yaxis_title="ATR", template="plotly_dark")
    return fig

def plot_m15_price(df: pl.DataFrame, pair: str) -> go.Figure:
    """M15 価格チャートを生成する"""
    fig = go.Figure()
    if df.is_empty() or "time" not in df.columns or "close" not in df.columns: return fig
    fig.add_trace(go.Scatter(x=df["time"].to_pandas(), y=df["close"].to_pandas(), mode="lines", name=f"{pair} M15 価格", line=dict(color="#FFA500")))
    fig.update_layout(title=f"{pair} M15 価格チャート", yaxis_title="価格", template="plotly_dark", xaxis=dict(rangeslider=dict(visible=True)))
    return fig

def plot_trend_strength(df: pl.DataFrame) -> go.Figure:
    """トレンド強度の推移グラフを生成する"""
    fig = go.Figure()
    if df.is_empty() or "trend_strength" not in df.columns: return fig
    fig.add_trace(go.Scatter(x=df["ts"].to_pandas(), y=df["trend_strength"].to_pandas(), mode="lines", name="トレンド強度", line=dict(color="#FF00FF")))
    fig.update_layout(title="トレンド強度の推移", yaxis_title="強度", template="plotly_dark", xaxis=dict(rangeslider=dict(visible=True)))
    return fig

def run_coroutine(coro):
    import asyncio
    import threading
    result, exception = None, None
    def worker():
        nonlocal result, exception
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(coro)
        except Exception as e:
            exception = e
        finally:
            loop.close()
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    if exception: raise exception
    return result

def execute_close_sync(trade_id, units):
    import dashboard_data as db_data
    return run_coroutine(db_data.execute_trade_close(trade_id, units))

def execute_close_all_sync():
    import dashboard_data as db_data
    return run_coroutine(db_data.execute_close_all())

def display_performance_metrics(summary_data: dict, positions_data: list, trade_logs: pl.DataFrame, selected_pair: str, data_source: str, system_health: dict):
    """「パフォーマンス & ライブ」タブのUIを描画する"""
    st.header("📊 パフォーマンス & ライブ")

    st.subheader("口座サマリー")
    col1, col2, col3 = st.columns(3)

    account_info = summary_data.get('account', {})
    nav_value = float(account_info.get('NAV', 0))
    unrealized_pl = float(account_info.get('unrealizedPL', 0))
    margin_used = float(account_info.get('marginUsed', 0))

    col1.metric("口座残高 (NAV)", f"{nav_value:,.0f} JPY")
    col2.metric("含み損益", f"{unrealized_pl:,.0f} JPY")
    col3.metric("証拠金使用率", f"{(margin_used / (nav_value + 1e-9)) * 100:.2f} %")

    # --- 手動介入：保有ポジションと強制決済 ---
    st.subheader("保有ポジション")
    if positions_data:
        st.dataframe(positions_data, width="stretch")
        
        # --- 【タスク4追加】緊急時の強制決済UI ---
        with st.expander("⚠️ 緊急アクション (強制決済)", expanded=False):
            st.warning("誤操作に注意してください。実行するとOANDAサーバーへ成行決済リクエストが送信されます。")
            
            # 個別決済
            trade_ids = [str(p.get("id")) for p in positions_data if "id" in p]
            if trade_ids:
                col1, col2, col3 = st.columns([2, 1, 1])
                target_id = col1.selectbox("決済対象Trade ID", trade_ids, key="close_target_id")
                confirm_single = col2.checkbox("強制決済します。よろしいでしょうか？", key="confirm_single")
                if col3.button("個別決済を実行", type="primary", disabled=not confirm_single, use_container_width=True):
                    st.session_state.action_close_trade = target_id
            
            st.divider()
            
            # 全決済
            col4, col5 = st.columns([2, 1])
            confirm_all = col4.checkbox("【危険】すべての通貨ペアの全ポジションを強制決済します。よろしいでしょうか？", key="confirm_all")
            if col5.button("全ポジション一括決済", type="primary", disabled=not confirm_all, use_container_width=True):
                st.session_state.action_close_all = True
    else:
        st.info("現在保有中のポジションはありません。")

    st.subheader("取引実績")
    if not trade_logs.is_empty():
        st.dataframe(trade_logs.to_pandas(), width="stretch")
        st.plotly_chart(plot_equity_curve(trade_logs, selected_pair, data_source), width="stretch", key=f"equity_curve_{selected_pair}_{data_source}")
    else:
        st.info("まだ取引データがありません。")

def display_chart_analysis_and_logs(m15_df: pl.DataFrame, m5_atr_data: tuple, trend_strength_df: pl.DataFrame, system_logs: list, order_logs_df: pl.DataFrame, benchmark_data_df: pl.DataFrame, selected_pair: str):
    """「チャート分析 & ログ」タブのUIを描画する"""
    st.header("📈 チャート分析 & ログ")

    st.subheader("価格チャート")
    st.plotly_chart(plot_m15_price(m15_df, selected_pair), width="stretch", key=f"m15_price_{selected_pair}")

    st.subheader("ボラティリティ分析")
    times, atr_values, threshold = m5_atr_data
    st.plotly_chart(plot_m5_atr(times, atr_values, threshold), width="stretch", key=f"m5_atr_{selected_pair}")

    st.subheader("トレンド強度")
    st.plotly_chart(plot_trend_strength(trend_strength_df), width="stretch", key=f"trend_strength_{selected_pair}")

    st.subheader("システムログ")
    if system_logs:
        st.json(system_logs)
    else:
        st.info("システムログはありません。")

    st.subheader("注文ログ")
    if not order_logs_df.is_empty():
        st.dataframe(order_logs_df.to_pandas(), width="stretch")
    else:
        st.info("注文ログはありません。")

    st.subheader("ベンチマークデータ")
    if not benchmark_data_df.is_empty():
        st.dataframe(benchmark_data_df.to_pandas(), width="stretch")
    else:
        st.info("ベンチマークデータはありません。")



def calculate_balsara_ruin_prob(win_rate: float, risk_reward_ratio: float, risk_pct: float) -> float:
    """バルサラの破産確率の近似計算"""
    if win_rate <= 0 or risk_reward_ratio <= 0: return 100.0
    if win_rate == 1.0: return 0.0
    expected_value = (win_rate * risk_reward_ratio) - (1 - win_rate)
    if expected_value <= 0: return 100.0
    x = 1.0 - (expected_value / risk_reward_ratio)
    if x <= 0: return 0.0
    ruin_prob = (x ** (100.0 / risk_pct)) * 100.0
    return min(ruin_prob, 100.0)

def display_virtual_forward_test(virtual_logs: pl.DataFrame):
    """「仮想フォワード検証」タブのUIを描画する"""
    st.header("🧪 仮想フォワード検証 (JST表記)")

    with st.expander("🛠️ 過去のデータをアーカイブしてリセット", expanded=False):
        st.write("現在表示されている今週のフォワード検証データを専用のアーカイブファイル（`virtual_forward_history.csv`）に追記保存し、現在の表示データを初期状態にリセットします。過去の大切な実績データを失うことなく、次週のテストをクリーンな状態で始められます。")
        confirm_reset = st.checkbox("本当に今週のデータをアーカイブしてリセットする", value=False, key="confirm_reset_virtual")
        if st.button("🔄 アーカイブ＆リセットを実行", disabled=not confirm_reset):
            with st.spinner("データをアーカイブしています..."):
                from dashboard_data import archive_and_reset_virtual_forward
                import time
                success = archive_and_reset_virtual_forward()
                if success:
                    st.success("アーカイブとリセットが完了しました！")
                    time.sleep(1.5)
                    st.rerun()
                else:
                    st.error("アーカイブ処理中にエラーが発生しました。")

    # --- ▼▼▼ 保有中ポジションのリアルタイム表示 ▼▼▼ ---
    # 絶対パス指定へ修正
    open_pos_file = cfg.project_root / "data" / "virtual_open_positions.json"
    if open_pos_file.exists():
        try:
            with open(open_pos_file, "r", encoding="utf-8") as f:
                open_positions = json.load(f)

            if open_positions:
                st.subheader("🔥 現在保有中のポジション (リアルタイム)")
                df_open = pd.DataFrame(open_positions)

                # 日本時間へ変換
                jst = pytz.timezone('Asia/Tokyo')
                if "open_time" in df_open.columns:
                    df_open["open_time"] = pd.to_datetime(df_open["open_time"], format='mixed', errors='coerce').dt.tz_convert(jst).dt.strftime('%Y/%m/%d %H:%M')

                # 総合含み損益の計算とハイライト表示
                total_unrealized_pnl = df_open["unrealized_pnl"].sum()
                pnl_color = "normal" if total_unrealized_pnl >= 0 else "inverse"

                st.metric(
                    "現在の総合含み損益",
                    f"{int(total_unrealized_pnl):,} 円",
                    delta=f"{int(total_unrealized_pnl):,} 円",
                    delta_color=pnl_color
                )

                # 表示用フォーマットの整形
                if "lot_size" in df_open.columns:
                    df_open["lot_size"] = df_open["lot_size"].apply(lambda x: f"{x/100000:.2f} Lot")
                if "unrealized_pnl" in df_open.columns:
                    df_open["unrealized_pnl"] = df_open["unrealized_pnl"].apply(lambda x: f"{int(x):,} 円")

                rename_open_cols = {
                    "open_time": "エントリー日時", "pair": "通貨ペア", "direction": "売買",
                    "lot_size": "数量", "entry_price": "建値", "current_price": "現在値",
                    "sl_price": "現在の損切(SL)", "tp_price": "目標利確(TP)", "unrealized_pnl": "含み損益"
                }
                df_open = df_open.rename(columns=rename_open_cols)
                display_open_cols = [col for col in rename_open_cols.values() if col in df_open.columns]

                st.dataframe(df_open[display_open_cols], width="stretch", hide_index=True)
                st.markdown("---")

        except Exception as e:
            st.warning(f"保有中ポジションの読み込みに失敗しました: {e}")

    if virtual_logs.is_empty():
        st.info("決済済みの仮想取引データがまだありません。")
        return

    df_display = virtual_logs.sort("timestamp", descending=True).to_pandas()

    # JSTへの変換
    jst = pytz.timezone('Asia/Tokyo')
    for col in ["timestamp", "entry_time"]:
        if col in df_display.columns:
            df_display[col] = pd.to_datetime(df_display[col], format='mixed', errors='coerce').dt.tz_convert(jst).dt.strftime('%Y/%m/%d %H:%M')

    # KPIとバルサラ計算
    total_profit = df_display["profit_amount"].sum()
    last_balance = 1000000.0 + total_profit
    total_trades = len(df_display)

    wins_df = df_display[df_display["profit_amount"] > 0]
    losses_df = df_display[df_display["profit_amount"] <= 0]

    win_rate = len(wins_df) / total_trades if total_trades > 0 else 0.0
    avg_win = wins_df["profit_amount"].mean() if not wins_df.empty else 0
    avg_loss = abs(losses_df["profit_amount"].mean()) if not losses_df.empty else 1
    rr_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

    ruin_prob = calculate_balsara_ruin_prob(win_rate, rr_ratio, risk_pct=2.0)

    # UI描画
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("仮想口座残高", f"{last_balance:,.0f} 円")
    col2.metric("総損益", f"{total_profit:,.0f} 円", delta=f"{total_profit:,.0f}")
    col3.metric("勝率 (RR比)", f"{win_rate*100:.1f} % ({rr_ratio:.2f})")

    ruin_color = "normal" if ruin_prob < 1.0 else "inverse" if ruin_prob < 10.0 else "off"
    col4.metric("バルサラの破産確率", f"{ruin_prob:.2f} %", delta="危険域" if ruin_prob > 1.0 else "安全", delta_color=ruin_color)

    if ruin_prob > 5.0:
        st.error("⚠️ **警告**: 現在の勝率と損益比では、長期的に口座が破産する確率が5%を超えています。")

    st.subheader("詳細な仮想取引履歴 (決済済み)")

    if "reason" in df_display.columns:
        reason_map = {
            "TP": "🟢 利確 (TP)",
            "SL": "🔴 損切 (SL)",
            "TSL": "🛡️ トレール利確",
            "TSL Hit": "🛡️ TSL利確 (追従決済)",
            "Flash Exit": "🚨 緊急脱出",
            "Weekend Close": "⏳ 週末一括決済",
            "Doten Reverse": "🔄 ドテン決済",
            "Timeout": "⏱ 時間切れ"
        }
        df_display["reason"] = df_display["reason"].map(lambda x: reason_map.get(x, x))

    # スプレッドの表示フォーマット
    if "spread_pips" in df_display.columns:
        df_display["spread_pips"] = df_display["spread_pips"].apply(lambda x: f"{x:.1f} pips" if pd.notnull(x) else "-")

    for col in ["new_balance", "profit_amount"]:
        if col in df_display.columns:
            df_display[col] = df_display[col].apply(lambda x: f"{int(x):,} 円")

    if "lot_size" in df_display.columns:
        df_display["lot_size"] = df_display["lot_size"].apply(lambda x: f"{x/100000:.2f} Lot")

    rename_cols = {
        "timestamp": "決済日時", "entry_time": "エントリー日時", "pair": "通貨ペア",
        "direction": "売買", "strategy": "戦略", "ai_score": "AIスコア",
        "spread_pips": "スプレッド",  # 👈 追加
        "lot_size": "数量", "entry_price": "建値", "exit_price": "決済値",
        "profit_amount": "損益額", "new_balance": "口座残高", "reason": "決済理由"
    }

    df_display = df_display.rename(columns=rename_cols)
    display_cols = [col for col in rename_cols.values() if col in df_display.columns]
    st.dataframe(df_display[display_cols], width="stretch", hide_index=True)

def display_news_and_indicators(news_data: list, economic_events: list):
    """「ニュース/指標」タブのUIを描画する"""
    st.header("📰 ニュース/指標")

    st.subheader("重要経済指標カレンダー")
    if economic_events:
        df_econ = pd.DataFrame(economic_events)
        if not df_econ.empty and 'impact' in df_econ.columns:
            df_econ = df_econ[df_econ['impact'].isin(['high', 'medium'])]

        if not df_econ.empty:
            df_econ["time"] = pd.to_datetime(df_econ["time"]).dt.tz_convert('Asia/Tokyo')
            display_cols_econ = ["time", "event", "currency", "impact"]
            df_econ = df_econ[display_cols_econ]

            def highlight_impact_econ(s):
                if s.impact == 'high':
                    return ['background-color: #660000'] * len(s)
                elif s.impact == 'medium':
                    return ['background-color: #666600'] * len(s)
                else:
                    return [''] * len(s)

            st.dataframe(df_econ.style.apply(highlight_impact_econ, axis=1), width="stretch")
        else:
            st.info("表示対象（重要度 中以上）の経済指標はありません。")
    else:
        st.info("予定されている重要経済指標はありません。")

    st.divider()

    st.subheader("最新ニュース")
    if news_data:
        df = pd.DataFrame(news_data)
        display_cols = ["time", "event", "currency", "description", "impact"]
        display_cols = [col for col in display_cols if col in df.columns]
        if display_cols:
            df = df[display_cols]

            def highlight_impact_news(s):
                if s.impact == 'high':
                    return ['background-color: #660000'] * len(s)
                elif s.impact == 'medium':
                    return ['background-color: #666600'] * len(s)
                else:
                    return [''] * len(s)

            st.dataframe(df.style.apply(highlight_impact_news, axis=1), width="stretch")
        else:
            st.info("表示するニュースがありません。")
    else:
        st.info("表示するニュースや指標がありません。")

# --- cronUI化用の日本語マッピングとヘルパー関数 ---
DOW_DISPLAY_MAP = {
    "*": "毎日", "1-5": "平日のみ", "0": "日曜日", "1": "月曜日", 
    "2": "火曜日", "3": "水曜日", "4": "木曜日", "5": "金曜日", "6": "土曜日"
}
REVERSE_DOW_MAP = {v: k for k, v in DOW_DISPLAY_MAP.items()}

def parse_cron_to_ui(cron_str: str) -> tuple[int, int, str]:
    default = (0, 0, "毎日")
    if not cron_str: return default
    parts = cron_str.strip().split()
    if len(parts) < 5: return default
    m_str, h_str, _, _, dow_str = parts[:5]
    return (int(m_str) if m_str.isdigit() else 0, int(h_str) if h_str.isdigit() else 0, DOW_DISPLAY_MAP.get(dow_str, "毎日"))

def build_cron_from_ui(m_val: int, h_val: int, dow_display: str) -> str:
    return f"{m_val:02d} {h_val:02d} * * {REVERSE_DOW_MAP.get(dow_display, '*')}"

def system_management_tab(job_statuses: dict = None):
    """「システム管理」タブのUIを描画する（全機能統合・日本語cron対応版）"""
    st.header("⚙️ システム管理")
    config_data = cfg.config

    # 1. 口座モード設定
    st.subheader("口座モード設定")
    oanda_config = config_data.get("oanda", {})
    current_profile = oanda_config.get("current_profile", "")
    profiles = oanda_config.get("profiles", {})
    profile_options = list(profiles.keys())

    try:
        current_index = profile_options.index(current_profile) if current_profile in profile_options else 0
    except ValueError:
        current_index = 0

    selected_profile = st.selectbox("現在のアクティブ口座:", profile_options, index=current_index, key="profile_selector")
    if selected_profile in profiles:
        st.caption(profiles[selected_profile].get("description", "説明がありません"))

    oanda_config["current_profile"] = selected_profile
    st.divider()

    # 2. テクニカル指標 パラメータ設定
    st.subheader("テクニカル指標 パラメータ設定")
    st.markdown("※ 取引ロジックの根幹となる指標の期間や閾値を直接変更できます。")
    indicators = config_data.get("indicators", {})
    
    col_i1, col_i2, col_i3 = st.columns(3)
    indicators["ema_short"] = col_i1.number_input("短期EMA 期間", 1, 50, int(indicators.get("ema_short", 5)), 1)
    indicators["ema_mid"] = col_i2.number_input("中期EMA 期間", 10, 100, int(indicators.get("ema_mid", 20)), 1)
    indicators["rsi_period"] = col_i3.number_input("RSI 期間", 2, 50, int(indicators.get("rsi_period", 14)), 1)

    col_i4, col_i5, col_i6 = st.columns(3)
    indicators["rsi_long"] = col_i4.number_input("RSI ロング(買い) 閾値", 10, 90, int(indicators.get("rsi_long", 55)), 1)
    indicators["rsi_short"] = col_i5.number_input("RSI ショート(売り) 閾値", 10, 90, int(indicators.get("rsi_short", 45)), 1)
    indicators["adx_th"] = col_i6.number_input("ADX トレンド判定 閾値", 10, 50, int(indicators.get("adx_th", 20)), 1)
    
    config_data["indicators"] = indicators
    st.divider()

    # 3. 定期実行タスク スケジュール管理 (日本語UI対応)
    st.subheader("定期実行タスク スケジュール管理")
    tasks = config_data.get("tasks", {})
    
    dow_options = list(DOW_DISPLAY_MAP.values())
    min_options = [0, 15, 30, 45]

    for task_name, task_config in tasks.items():
        with st.expander(f"🗓️ {task_name} - {task_config.get('description', '')}", expanded=False):
            task_config["enabled"] = st.toggle("このタスクを有効にする", value=task_config.get("enabled", False), key=f"toggle_task_{task_name}")
            
            if "schedule_summer" in task_config:
                st.markdown("**【夏時間】**")
                m_sum, h_sum, dow_sum = parse_cron_to_ui(task_config.get("schedule_summer"))
                c1, c2, c3 = st.columns(3)
                ui_dow_sum = c1.selectbox("実行曜日", dow_options, index=dow_options.index(dow_sum) if dow_sum in dow_options else 0, key=f"sum_dow_{task_name}")
                ui_h_sum = c2.number_input("時 (0-23)", 0, 23, h_sum, 1, key=f"sum_h_{task_name}")
                ui_m_sum = c3.selectbox("分", min_options, index=min_options.index(m_sum) if m_sum in min_options else 0, key=f"sum_m_{task_name}")
                task_config["schedule_summer"] = build_cron_from_ui(ui_m_sum, ui_h_sum, ui_dow_sum)

                st.markdown("**【冬時間】**")
                m_win, h_win, dow_win = parse_cron_to_ui(task_config.get("schedule_winter"))
                c4, c5, c6 = st.columns(3)
                ui_dow_win = c4.selectbox("実行曜日", dow_options, index=dow_options.index(dow_win) if dow_win in dow_options else 0, key=f"win_dow_{task_name}")
                ui_h_win = c5.number_input("時 (0-23)", 0, 23, h_win, 1, key=f"win_h_{task_name}")
                ui_m_win = c6.selectbox("分", min_options, index=min_options.index(m_win) if m_win in min_options else 0, key=f"win_m_{task_name}")
                task_config["schedule_winter"] = build_cron_from_ui(ui_m_win, ui_h_win, ui_dow_win)
            else:
                st.markdown("**【実行スケジュール】**")
                m_sch, h_sch, dow_sch = parse_cron_to_ui(task_config.get("schedule"))
                c7, c8, c9 = st.columns(3)
                ui_dow_sch = c7.selectbox("実行曜日", dow_options, index=dow_options.index(dow_sch) if dow_sch in dow_options else 0, key=f"sch_dow_{task_name}")
                ui_h_sch = c8.number_input("時 (0-23)", 0, 23, h_sch, 1, key=f"sch_h_{task_name}")
                ui_m_sch = c9.selectbox("分", min_options, index=min_options.index(m_sch) if m_sch in min_options else 0, key=f"sch_m_{task_name}")
                task_config["schedule"] = build_cron_from_ui(ui_m_sch, ui_h_sch, ui_dow_sch)
    st.divider()

    # 4. 主要取引パラメータ
    st.subheader("主要取引パラメータ")
    trading_params = config_data.get("trading", {})
    risk_params = config_data.get("risk_management", {})
    cols = st.columns(3)
    trading_params["enabled"] = cols[0].toggle("**システム全体の取引を有効化**", value=trading_params.get("enabled", True), key="toggle_trade_master")
    trading_params["risk_per_trade"] = cols[1].number_input("1取引あたりのリスク (%)", 0.01, 5.0, float(trading_params.get("risk_per_trade", 0.01)) * 100, 0.01, "%.2f") / 100
    risk_params["max_positions_total"] = cols[2].number_input("最大合計ポジション数", 1, 10, int(risk_params.get("max_positions_total", 4)), 1)
    st.divider()

    # 5. 通貨ペア別 取引有効化設定
    st.subheader("通貨ペア別 取引有効化設定")
    trade_enabled_pairs = trading_params.get("trade_enabled_pairs", {})
    symbols_list = trading_params.get("symbols", ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY"])
    num_columns = 4
    cols = st.columns(num_columns)
    for i, symbol in enumerate(symbols_list):
        with cols[i % num_columns]:
            new_status = st.toggle(symbol, value=trade_enabled_pairs.get(symbol, False), key=f"toggle_pair_{symbol}")
            trade_enabled_pairs[symbol] = new_status
    st.divider()

    # 6. 時間帯フィルター ＆ 週末停止設定
    st.subheader("時間帯フィルター ＆ 週末停止設定")
    st.markdown("※ 流動性が低下しスプレッドが広がる「魔の時間帯」の新規エントリーを禁止します。（すべて日本時間）")
    
    if "time_filters" not in config_data.get("system", {}):
        config_data.setdefault("system", {})["time_filters"] = {"forbidden_start": "01:00", "forbidden_end": "09:00", "friday_stop_hour": 20}
    time_filters = config_data["system"]["time_filters"]

    col_t1, col_t2, col_t3 = st.columns(3)
    start_time_val = pd.to_datetime(time_filters.get("forbidden_start", "01:00")).time()
    end_time_val = pd.to_datetime(time_filters.get("forbidden_end", "09:00")).time()
    
    new_f_start = col_t1.time_input("⛔ エントリー禁止 開始 (JST)", value=start_time_val)
    new_f_end = col_t2.time_input("⛔ エントリー禁止 終了 (JST)", value=end_time_val)
    new_fri_stop = col_t3.number_input("🛑 金曜 新規停止時間 (JST 〇時)", 0, 23, int(time_filters.get("friday_stop_hour", 20)), 1)
    st.divider()

    # 7. 設定保存ボタン
    if st.button("システム管理設定を保存", type="primary", width="stretch"):
        try:
            cfg.config["oanda"] = oanda_config
            cfg.config["indicators"] = indicators
            cfg.config["tasks"] = tasks
            trading_params["trade_enabled_pairs"] = trade_enabled_pairs
            cfg.config["trading"] = trading_params
            cfg.config["risk_management"] = risk_params
            
            time_filters["forbidden_start"] = new_f_start.strftime("%H:%M")
            time_filters["forbidden_end"] = new_f_end.strftime("%H:%M")
            time_filters["friday_stop_hour"] = new_fri_stop
            cfg.config["system"]["time_filters"] = time_filters
            
            cfg.save_config()
            st.success("✅ 設定が正常に保存されました。次回のループから反映されます。")
            st.toast("設定を保存しました", icon="💾")
        except Exception as e:
            st.error(f"❌ 保存中にエラーが発生しました: {e}")

def risk_and_money_management_tab():
    """「リスク・資金管理」タブのUIを描画する"""
    st.header("🛡️ リスク・資金管理 (Money Management)")
    config_data = cfg.config
    
    if "risk_management" not in config_data:
        config_data["risk_management"] = {}
    risk_params = config_data["risk_management"]

    st.subheader("資金管理設定 (Money Management Inputs)")
    st.markdown("※ エクセルの数式に基づき、入力項目から投資額(Lot)と期待値を自動算出します。")
    
    col1, col2, col3 = st.columns(3)
    allocated_margin = col1.number_input("割り当て証拠金/ペア (円)", 10000, 10000000, int(risk_params.get("allocated_margin_per_pair", 250000)), 10000)
    risk_params["allocated_margin_per_pair"] = allocated_margin

    invest_ratio_pct = col2.number_input("証拠金に対する投資割合 (%)", 1.0, 100.0, float(risk_params.get("invest_ratio", 0.5) * 100 if risk_params.get("invest_ratio") else 50.0), 1.0)
    risk_params["invest_ratio"] = invest_ratio_pct / 100.0

    risk_tolerance_pct = col3.number_input("証拠金に対するリスク許容割合 (%)", 0.1, 100.0, float(risk_params.get("risk_tolerance_pct", 0.015) * 100 if "risk_tolerance_pct" in risk_params else 1.5), 0.1)
    risk_params["risk_tolerance_pct"] = risk_tolerance_pct / 100.0

    col4, col5, col6 = st.columns(3)
    win_rate_pct = col4.number_input("想定勝率 (%)", 1.0, 100.0, float(risk_params.get("target_win_rate", 0.55) * 100 if risk_params.get("target_win_rate", 0.55) <= 1.0 else risk_params.get("target_win_rate", 55.0)), 1.0)
    risk_params["target_win_rate"] = win_rate_pct / 100.0
    
    target_rr_ratio = col5.number_input("利益割合 (損益1に対し)", 0.1, 10.0, float(risk_params.get("target_rr_ratio", 1.2)), 0.1)
    risk_params["target_rr_ratio"] = target_rr_ratio

    trades_per_day = col6.number_input("1日あたりの想定トレード回数", 1, 50, int(risk_params.get("trades_per_day", 5)), 1)
    risk_params["trades_per_day"] = trades_per_day
    
    col7, col8 = st.columns(2)
    base_sl_pips = col7.number_input("基本リスク値幅 (SL: pips)", 1.0, 100.0, float(risk_params.get("base_sl_pips", 10.0)), 1.0)
    risk_params["base_sl_pips"] = base_sl_pips

    st.markdown("**証拠金率 (%) [各通貨ペア]**")
    symbols = config_data.get("trading", {}).get("symbols", ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY"])
    margin_rates = risk_params.get("margin_rates", {})
    cols_margin = st.columns(len(symbols) if symbols else 4)
    for i, symbol in enumerate(symbols):
        default_margin = 0.05 if "GBP" in symbol else 0.04
        current_margin = margin_rates.get(symbol, default_margin)
        disp_margin = current_margin * 100 if current_margin <= 1.0 else current_margin
        margin_rates[symbol] = cols_margin[i].number_input(f"{symbol}", 1.0, 20.0, float(disp_margin), 1.0, key=f"margin_{symbol}") / 100
    risk_params["margin_rates"] = margin_rates

    st.divider()

    # ＝＝＝ ▼ リアルタイム計算シミュレーター ▼ ＝＝＝
    theoretical_risk_jpy = allocated_margin * (risk_tolerance_pct / 100.0)
    raw_lot = theoretical_risk_jpy / (base_sl_pips * 1000)
    actual_lot = math.floor(raw_lot * 100) / 100.0
    actual_lot_volume = actual_lot * 100000
    actual_risk_jpy = actual_lot * base_sl_pips * 1000
    target_profit_jpy = actual_risk_jpy * target_rr_ratio
    win_rate = win_rate_pct / 100.0
    expected_value = (target_profit_jpy * win_rate) - (actual_risk_jpy * (1 - win_rate))
    monthly_profit = expected_value * trades_per_day * 20 

    st.subheader("📊 期待値シミュレーション (USD/JPY例)")
    st.info(
        f"**1トレードの許容損失額**: **-{actual_risk_jpy:,.0f}** 円 (SL: {base_sl_pips:.1f} pips)\n\n"
        f"**算出された投資額 (Lot)**: 約 **{actual_lot_volume:,.0f}** 通貨 (**{actual_lot:,.2f} Lot**)\n\n"
        f"**1勝あたりの利益額**: **+{target_profit_jpy:,.0f}** 円 (TP: {base_sl_pips * target_rr_ratio:.1f} pips)\n\n"
        f"**1トレードの期待値**: **{expected_value:,.0f}** 円\n\n"
        f"**月間見込み利益 (1ペア)**: **{monthly_profit:,.0f}** 円 (※{trades_per_day}回×20日 想定)"
    )
    st.divider()

    st.subheader("ATR連動 ボラティリティ調整 (Dynamic Lot & TP/SL)")
    risk_params["use_dynamic_atr"] = st.toggle("ボラティリティによる動的調整を有効にする", value=risk_params.get("use_dynamic_atr", True))
    st.caption("※ ONにすると、相場が荒れている時はSL幅を広げてロットを減らし、凪の時はSL幅を狭めてロットを増やします。（許容損失額は常に一定に保たれます。ATRによるブロック機能は廃止されます）")
    st.divider()
    
    # ＝＝＝ ▼ 新規追加・差し替え箇所 ▼ ＝＝＝
    st.subheader("TP到達後 トレール設定 (Post-TP Trailing)")
    st.markdown("※ 設定したTP（利確目標）に到達した際、即座に決済せず、利益を伸ばすためのトレールに移行します。")
    tp_trail = risk_params.get("tp_trailing", {})
    
    tp_trail["enabled"] = st.toggle("TPトレールを有効にする", value=tp_trail.get("enabled", True))
    tp_trail["use_atr_trail"] = st.toggle("ATR連動トレールを使用する (推奨)", value=tp_trail.get("use_atr_trail", True), help="ONにすると、固定pipsではなく相場のボラティリティ(ATR)に合わせてトレール幅が自動伸縮し、ノイズで狩られるのを防ぎます。")
    
    col_t1, col_t2 = st.columns(2)
    tp_trail["trail_distance_pips"] = col_t1.number_input("固定トレール幅 (TPからマイナス pips)", 1.0, 100.0, float(tp_trail.get("trail_distance_pips", 10.0)), 1.0, help="ATR連動がOFFの場合に使用されます。")
    tp_trail["trail_step_pips"] = col_t2.number_input("固定SL更新ステップ (pips)", 1.0, 50.0, float(tp_trail.get("trail_step_pips", 5.0)), 1.0)
    
    risk_params["tp_trailing"] = tp_trail
    st.divider()

    st.subheader("ドテン（途転）決済設定")
    risk_params["enable_doten"] = st.toggle("ドテン売買を有効にする (機会損失の完全防止)", value=risk_params.get("enable_doten", True), help="ポジション保有中に逆方向の強いサインが出た場合、現在のポジションを強制決済して即座に波に乗り換えます。")
    st.divider()
    # ＝＝＝ ▲ 差し替えここまで ▲ ＝＝＝

    st.subheader("防衛システム設定 (Cooldown & GuardCenter)")
    cols_def = st.columns(2)
    risk_params["cooldown_minutes"] = cols_def[0].number_input("同方向再エントリー禁止 (分)", 0, 1440, int(risk_params.get("cooldown_minutes", 30)), 5)
    
    current_ks = risk_params.get("kill_switch_pips", 0.20)
    disp_ks = current_ks * 100 if current_ks <= 1.0 else current_ks
    risk_params["kill_switch_pips"] = cols_def[1].number_input("異常変動の検知幅 (pips)", 5.0, 200.0, float(disp_ks), 1.0) / 100
    risk_params["kill_switch_lockout_min"] = cols_def[1].number_input("異常検知後のロックアウト (分)", 5, 1440, int(risk_params.get("kill_switch_lockout_min", 60)), 5)

    if st.button("リスク管理設定を保存", type="primary", width="stretch", key="save_risk_params"):
        try:
            # 廃止された古い step_tsl の設定を削除してスッキリさせる
            if "step_tsl" in risk_params:
                del risk_params["step_tsl"]
            
            cfg.config["risk_management"].update(risk_params)
            cfg.save_config()
            st.success("✅ 設定は正常に保存されました！（TP到達後トレール設定を適用します）")
            st.toast("設定を保存しました", icon="💾")
        except Exception as e:
            st.error(f"❌ 保存中にエラーが発生しました: {e}")

def display_ai_analysis_tab(optimization_results=None, job_statuses=None):
    """「AI分析＆レポート」タブのUIを描画する"""
    import streamlit as st
    import pandas as pd
    import asyncio
    import threading
    from pathlib import Path
    from datetime import datetime, timezone
    from core import cfg

    # 非同期関数を別スレッドで安全に実行するための内部ヘルパー
    def _run_async_safe(coro):
        result = None
        exception = None
        def worker():
            nonlocal result, exception
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(coro)
            except Exception as e:
                exception = e
            finally:
                loop.close()
        t = threading.Thread(target=worker)
        t.start()
        t.join()
        if exception:
            raise exception
        return result
        
    st.header("🤖 AI分析＆レポート")
    
    if optimization_results is not None:
        st.subheader("💡 最新の最適化（WFA）結果")
        if not optimization_results.empty:
            st.dataframe(optimization_results[["通貨ペア", "戦略", "最適化日時", "最適パラメータ"]], width="stretch")
        else:
            st.info("まだ最適化提案がありません。")
        st.divider()
        
    # ロット配分（Multiplier）の表示
    st.subheader("📊 最新の通貨ペア別ロット配分 (Multiplier)")
    # config.jsonから最新の傾斜配分を取得
    import json
    config_path = Path(__file__).resolve().parent / "config.json"
    pair_mults = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cdata = json.load(f)
            pair_mults = cdata.get("dynamic_allocation", {}).get("pair_multipliers", {})
        except Exception:
            pass
            
    if pair_mults:
        # 横並びのメトリクスとして表示
        cols = st.columns(len(pair_mults))
        for col, (pair, mult) in zip(cols, pair_mults.items()):
            col.metric(pair, f"{mult:.2f}x")
    else:
        st.info("まだ自動計算されたロット配分がありません。")
        
    st.divider()
    
    # 📝 WFAパイプライン 最新バックテスト結果とAI分析
    wfa_report_path = Path(__file__).resolve().parent / "data" / "wfa_latest_analysis.txt"
    if wfa_report_path.exists():
        st.subheader("📝 最新の WFA バックテスト詳細レポート")
        with open(wfa_report_path, "r", encoding="utf-8") as f:
            wfa_report_text = f.read()
            
        with st.expander("📄 WFA バックテスト詳細レポートを開く", expanded=False):
            st.text(wfa_report_text)
            
        csv_path = str(Path(__file__).resolve().parent / "data" / "reports" / "decision_log_multi_pairs.csv")
        display_backtest_visuals(csv_path)
            
        if st.button("🤖 WFA結果をAIに分析させる", type="primary", key="btn_wfa_ai"):
            with st.spinner("AIアナリストがWFAレポートを分析中..."):
                from core.ai_strategy_analyzer import request_ai_analysis
                ai_result = _run_async_safe(request_ai_analysis(wfa_report_text))
                st.session_state.wfa_ai_result = ai_result
                st.rerun()
                
        if "wfa_ai_result" in st.session_state:
            st.markdown("### 🧠 WFA AI アナリストの見解")
            ai_res = st.session_state.wfa_ai_result
            st.info(f"**【要約】**\n{ai_res.get('summary', '')}")
            rec = ai_res.get("recommendation", {})
            st.success(f"**【推奨調整パラメータ】**: `{rec.get('parameter_to_tune', '')}`\n\n**【理由】**: {rec.get('reason', '')}")
            
        st.divider()

    # 🚀 バックテスト & WFA 実行コンソール (システム管理から移設)
    import dashboard_data as db_data
    
    st.subheader("🚀 バックテスト & WFA 実行コンソール")
    st.markdown("※ コンソールを開くことなく、バックグラウンドで重い処理を非同期実行します。")
    
    if job_statuses is None:
        job_statuses = {"backtest": False, "wfa": False}
        
    bt_running = job_statuses.get("backtest", False)
    wfa_running = job_statuses.get("wfa", False)

    col_j1, col_j2 = st.columns(2)
    
    with col_j1:
        st.markdown("#### 📊 バックテスト (OHLC Replay)")
        st.caption("※ 任意の期間を指定して、現在のロジックの長期検証(R&D)を行います。")
        
        from datetime import datetime
        import calendar
        
        now = datetime.now()
        first_day = datetime(now.year, now.month, 1)
        last_day = datetime(now.year, now.month, calendar.monthrange(now.year, now.month)[1])
        
        col_d1, col_d2 = st.columns(2)
        start_date = col_d1.date_input("開始日", value=first_day, key="rnd_start")
        end_date = col_d2.date_input("終了日", value=last_day, key="rnd_end")
        
        bt_progress = float(job_statuses.get("backtest_progress", 0.0))
        bt_phase = job_statuses.get("backtest_phase", "実行中...")
        
        if bt_running:
            st.button("🔄 バックテスト実行中...", disabled=True, width="stretch", key="btn_bt_run")
            st.progress(max(0.01, min(1.0, bt_progress)), text=f"⏳ {bt_phase}")
        else:
            if st.button("▶️ R&D バックテスト開始", type="primary", width="stretch", key="btn_bt_start"):
                start_str = start_date.strftime("%Y-%m-%d 00:00:00")
                end_str = end_date.strftime("%Y-%m-%d 23:59:59")
                if db_data.start_backtest_job(start_str, end_str):
                    st.success("✅ R&Dバックテストをバックグラウンドで開始しました。完了までお待ちください。")
                    st.rerun()
                else:
                    st.error("❌ 既に実行中です。")

    with col_j2:
        st.markdown("#### 🤖 週次4段階連鎖パイプライン (WFA最適化)")
        st.caption("※ ローカル最新データから最適化月数を自動算出し、傾斜配分更新まで一括連鎖実行します。")
        
        pipe_phase = job_statuses.get("pipeline_phase", "")
        pipe_progress = float(job_statuses.get("pipeline_progress", 0.0))
        pipe_status = job_statuses.get("pipeline_status", "idle")
        pipe_updated = job_statuses.get("pipeline_updated_at", "")

        if wfa_running:
            if "第3段階" in pipe_phase:
                bt_progress = float(job_statuses.get("backtest_progress", 0.0))
                bt_phase = job_statuses.get("backtest_phase", "")
                # WFA全体の進捗(50%〜90%)の中にバックテストの進捗(0%〜100%)をマッピング
                actual_progress = 0.50 + (bt_progress * 0.40)
                st.progress(max(0.01, min(1.0, actual_progress)), text=f"⏳ {pipe_phase} [{bt_phase}]")
            else:
                st.progress(max(0.05, min(1.0, pipe_progress)), text=f"⏳ {pipe_phase or '実行中...'}")
                
            st.button("🔄 パイプライン実行中...", disabled=True, width="stretch", key="btn_wfa_run")
        else:
            if pipe_status == "completed":
                st.caption(f"✅ 前回完了: {pipe_updated or '直近'} ({pipe_phase})")
            elif pipe_status == "failed":
                st.caption(f"⚠️ 前回エラー: {pipe_phase}")
            
            if st.button("▶️ WFA パイプライン手動実行", type="primary", width="stretch", key="btn_wfa_start"):
                if db_data.start_wfa_job():
                    st.success("✅ 週次パイプライン（WFA連鎖）をバックグラウンドで開始しました。")
                    st.rerun()
                else:
                    st.error("❌ 既に実行中です。")
    st.divider()

    # 🔬 手動検証 (R&D) レポート
    st.subheader("🔬 手動検証 (R&D) レポート")
    rnd_report_path = Path(__file__).resolve().parent / "data" / "rnd_latest_analysis.txt"
    if rnd_report_path.exists():
        with open(rnd_report_path, "r", encoding="utf-8") as f:
            rnd_report_text = f.read()
        
        with st.expander("📄 最新の R&D バックテスト詳細レポートを開く", expanded=False):
            st.text(rnd_report_text)
            
        csv_path = str(Path(__file__).resolve().parent / "data" / "reports" / "decision_log_multi_pairs.csv")
        display_backtest_visuals(csv_path)
            
        if st.button("🤖 この結果をAIに分析させる", type="primary"):
            with st.spinner("AIアナリストがレポートを分析中..."):
                from core.ai_strategy_analyzer import request_ai_analysis
                ai_result = _run_async_safe(request_ai_analysis(rnd_report_text))
                st.session_state.rnd_ai_result = ai_result
                st.rerun()
                
        if "rnd_ai_result" in st.session_state:
            st.markdown("### 🧠 AI アナリストの見解")
            ai_res = st.session_state.rnd_ai_result
            st.info(f"**【要約】**\n{ai_res.get('summary', '')}")
            rec = ai_res.get("recommendation", {})
            st.success(f"**【推奨調整パラメータ】**: `{rec.get('parameter_to_tune', '')}`\n\n**【理由】**: {rec.get('reason', '')}")
            
    else:
        st.info("現在、表示できる手動検証(R&D)レポートはありません。上の「R&D バックテスト開始」を実行してください。")
    st.divider()

    # 提案履歴の表示
    st.subheader("3. 過去の提案履歴")
    log_dir = cfg.get_sync("system.log_dir", "logs")
    prop_file = Path(log_dir) / f"ai_proposals_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    if prop_file.exists():
        try:
            df_prop = pd.read_csv(prop_file)
            st.dataframe(df_prop, width="stretch")
        except Exception as e:
            st.warning(f"提案履歴の読み込みに失敗しました: {e}")
    else:
        st.info("今日の提案履歴はありません。")

def display_backtest_visuals(csv_path_str: str):
    """バックテストのCSVを読み込み、フォワード検証風のUIで表示する"""
    import streamlit as st
    import pandas as pd
    from pathlib import Path
    
    csv_path = Path(csv_path_str)
    if not csv_path.exists():
        return
        
    try:
        df = pd.read_csv(csv_path)
        if df.empty:
            return
            
        st.markdown("#### 📊 バックテスト取引実績 (ビジュアル表示)")
        
        # 指標の計算
        wins = df[df["pnl"] > 0]
        losses = df[df["pnl"] <= 0]
        
        gross_profit = wins["pnl"].sum() if not wins.empty else 0.0
        gross_loss = abs(losses["pnl"].sum()) if not losses.empty else 0.0
        total_pnl = df["pnl"].sum()
        
        total_trades = len(df)
        win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0.0
        pf = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
        
        # 平均利益と平均損失からRR比を計算
        avg_win = wins["pnl"].mean() if not wins.empty else 0.0
        avg_loss = abs(losses["pnl"].mean()) if not losses.empty else 0.0
        rr_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("純損益 (Net PnL)", f"{int(total_pnl):,} 円", delta=f"{int(total_pnl):,} 円")
        col2.metric("勝率 (Win Rate)", f"{win_rate:.1f} %")
        pf_str = f"{pf:.2f}" if pf != float('inf') else "∞"
        col3.metric("プロフィットファクター", pf_str)
        col4.metric("リスクリワード比 (RR)", f"{rr_ratio:.2f}")
        
        with st.expander("📋 取引記録一覧 (全トレード詳細)", expanded=False):
            # 表示用のカラム名変更
            rename_cols = {
                "Time": "決済日時", "Pair": "通貨ペア", "Action": "アクション", 
                "Direction": "売買", "Price": "決済価格", 
                "pips": "獲得pips", "pnl": "損益(円)", "exit_reason": "決済理由"
            }
            df_display = df.rename(columns=rename_cols)
            display_cols = [col for col in rename_cols.values() if col in df_display.columns]
            
            # 足りないカラムがあれば元のカラム名も追加
            for col in df.columns:
                if col not in rename_cols:
                    display_cols.append(col)
                    
            st.dataframe(df_display[display_cols], width="stretch", hide_index=True)
            
    except Exception as e:
        st.warning(f"バックテスト結果のビジュアル表示に失敗しました: {e}")
