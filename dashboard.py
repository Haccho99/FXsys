"""dashboard.py (v8 - Refactored with UI layer for Integration)

このモジュールはダッシュボードの全体的な構造（メインループ）を管理する最上位ファイルです。
実際のデータ処理は`dashboard_data.py`に、UIコンポーネントの描画は`dashboard_ui.py`に
それぞれ分離されており、このファイルはそれらを呼び出して組み合わせる役割に徹します。
"""
import sys
import os
from pathlib import Path

# ==========================================
# 🚨 【重要】インポートパスの動的解決 
# 統合ポータル（C:\WealthSystem\main.py）から呼び出された際のエラーを回避
# ==========================================
current_dir = Path(__file__).resolve().parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

import streamlit as st
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone

# --- 内部モジュールのインポート ---
from core.config_manager import ConfigManager
cfg = ConfigManager(current_dir)
project_root = current_dir
import dashboard_data as db_data # データ処理モジュール
import dashboard_ui as db_ui   # UI描画モジュール

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

# --- ロガー設定 ---
log_dir = cfg.get_sync("system.log_dir", "logs")
if not os.path.isabs(log_dir):
    log_dir = os.path.join(current_dir, log_dir) # ログもFXsys内に確実に保存
os.makedirs(log_dir, exist_ok=True)
log_file = f"{log_dir}/dashboard_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=10, encoding="utf-8")
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'))
if not logger.handlers:
    logger.addHandler(handler)

# --- イベントループを再利用するヘルパー関数 ---
def run_async(coro):
    """
    非同期関数を同期的なコンテキストで実行するためのヘルパー。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)

async def main():
    # ⚠️ st.set_page_config は統合側の main.py で行うためここでは削除
    st.title("FX Trading System Dashboard")
    
    # --- セッション状態の初期化 ---
    if 'selected_pair' not in st.session_state:
        st.session_state.selected_pair = cfg.get_sync("trading.symbols", ["USD_JPY"])[0]
    if 'data_source' not in st.session_state:
        st.session_state.data_source = "リアルタイム"

    # --- 決済アクションのハンドリング (UI描画前に処理) ---
    if 'action_close_trade' in st.session_state:
        trade_id = st.session_state.action_close_trade
        del st.session_state.action_close_trade
        executor = await db_data.get_executor()
        success = await executor.close_trade(trade_id)
        if success:
            st.toast(f"Trade {trade_id} を決済しました。", icon="✅")
        else:
            st.toast("決済に失敗しました。", icon="❌")
        st.rerun()
            
    if 'action_close_all' in st.session_state:
        del st.session_state.action_close_all
        executor = await db_data.get_executor()
        success = await executor.close_all_positions()
        if success:
            st.toast("全ポジションを一括決済しました。", icon="✅")
        else:
            st.toast("一括決済に失敗しました。", icon="❌")
        st.rerun()

    # --- サイドバー (表示設定) ---
    with st.sidebar:
        st.header("表示設定")
        st.session_state.selected_pair = st.selectbox("通貨ペア", cfg.get_sync("trading.symbols", ["USD_JPY"]), index=cfg.get_sync("trading.symbols", ["USD_JPY"]).index(st.session_state.selected_pair))
        st.session_state.data_source = st.radio("データソース",["リアルタイム", "バックテスト"], index=0 if st.session_state.data_source == "リアルタイム" else 1)
        refresh_interval = st.select_slider("自動更新間隔 (秒)", options=[0, 5, 10, 30, 60], value=10)

    # --- 全てのデータ取得をこのメイン関数冒頭に集約 ---
    health_status, summary_data, positions_data, candidates_data, error_df, trade_logs, m15_df, m5_atr_data, trend_strength_df, system_logs, order_logs_df, benchmark_data_df, optimization_results, news_data, economic_events, virtual_trade_logs, system_health, job_statuses = await asyncio.gather(
        db_data.check_system_health_cached(ttl_seconds=30),
        db_data.fetch_account_summary(),
        db_data.fetch_open_positions(),
        db_data.fetch_signal_candidates(),
        db_data.load_system_errors_async(),
        asyncio.to_thread(db_data.load_trade_logs, datetime.now(timezone.utc).strftime('%Y%m%d')),
        asyncio.to_thread(db_data.load_m15_data, st.session_state.selected_pair),
        db_data.load_m5_atr_data(st.session_state.selected_pair),
        asyncio.to_thread(db_data.load_trend_strength, datetime.now(timezone.utc).strftime('%Y%m%d')),
        db_data.load_system_logs(),
        asyncio.to_thread(db_data.load_order_log, datetime.now(timezone.utc).strftime('%Y%m%d')),
        asyncio.to_thread(db_data.load_benchmark_data, datetime.now(timezone.utc).strftime('%Y%m%d')),
        asyncio.to_thread(db_data.load_optimization_results),
        db_data.load_news_and_indicators(),
        db_data.load_economic_calendar(),
        asyncio.to_thread(db_data.load_virtual_trade_logs),
        db_data.check_system_health(),
        db_data.get_job_statuses_async()
    )

    # --- ヘルスステータスの表示 ---
    db_ui.display_health_status(health_status)

    # --- WFA・週次パイプライン実行中の常時ステータスバナー ---
    if job_statuses.get("wfa"):
        phase_txt = job_statuses.get("pipeline_phase", "週次最適化パイプラインを実行中...")
        progress_pct = int(job_statuses.get("pipeline_progress", 0.0) * 100)
        st.info(f"🔄 **【週次自動パイプライン（WFA・傾斜配分最適化）実行中】** {phase_txt} ({progress_pct}%)", icon="⏳")

    # --- メインコンテンツ（タブ）の定義 ---
    tab1, tab2, tab_ai, tab_virtual, tab_news, tab_risk, tab_admin = st.tabs([
        "📊 パフォーマンス & ライブ", 
        "📈 チャート分析 & ログ", 
        "🤖 AI分析＆レポート", 
        "🧪 仮想検証", 
        "📰 ニュース/指標", 
        "🛡️ リスク・資金管理", 
        "⚙️ システム管理"
    ])

    with tab1:
        db_ui.display_performance_metrics(summary_data, positions_data, trade_logs, st.session_state.selected_pair, st.session_state.data_source, system_health)

    with tab2:
        db_ui.display_chart_analysis_and_logs(m15_df, m5_atr_data, trend_strength_df, system_logs, order_logs_df, benchmark_data_df, st.session_state.selected_pair)

    with tab_ai:
        db_ui.display_ai_analysis_tab(optimization_results, job_statuses)

    with tab_virtual:
        db_ui.display_virtual_forward_test(virtual_trade_logs)

    with tab_news:
        db_ui.display_news_and_indicators(news_data, economic_events)

    with tab_risk:
        db_ui.risk_and_money_management_tab()

    with tab_admin:
        db_ui.system_management_tab(job_statuses)
    
    # --- 自動更新設定 ---
    if st_autorefresh and refresh_interval > 0:
        st_autorefresh(interval=refresh_interval * 1000, key="dashboard_autorefresh")

# ⚠️ 統合ポータルからページとして呼び出された場合でも確実に実行されるように
# if __name__ == "__main__": のガードを外して直接呼び出します
run_async(main())