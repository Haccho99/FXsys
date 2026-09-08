"""dashboard_data.py (v2 - Streamlit-independent)

このモジュールは、ダッシュボードに表示するための全てのデータ取得・加工処理を担当します。
Redis、ログファイル、Parquetファイルなど、様々なデータソースから情報を読み込み、
UI（dashboard.py）が利用しやすい形に整えるのが主な役割です。
このバージョンでは、Streamlitへの依存が完全に排除されています。
"""
from __future__ import annotations
import polars as pl
import pandas as pd
import json
import os
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta
from io import StringIO
import numpy as np
from deep_translator import GoogleTranslator
import traceback
import streamlit as st
import time

# --- 内部モジュールのインポート ---
from core.config_manager import ConfigManager
project_root = Path(__file__).resolve().parent
cfg = ConfigManager(project_root)

from core.redis_client import create_redis_client as get_redis
from core.logger import log_error, get_logger
logger = get_logger(cfg, "dashboard_data")
from core.data import fetch_candles
from core.trade import get_executor

# --- データ取得・処理関数群 ---

class Rolling:
    """移動平均・標準偏差を計算するためのローリングウィンドウクラス"""
    def __init__(self, size: int):
        self.size, self.data = size, []
    def push(self, value: float):
        if value is not None and np.isfinite(value):
            self.data.append(value)
            if len(self.data) > self.size:
                self.data.pop(0)
    def mean(self) -> float:
        return np.mean(self.data) if self.data else 0.0
    def std(self) -> float:
        return np.std(self.data) if len(self.data) > 1 else 0.0

async def load_system_errors_async() -> pl.DataFrame:
    """Redisからシステムエラーログを取得する非同期関数"""
    try:
        redis_client = await get_redis(cfg, logger)
        if not redis_client:
            return pl.DataFrame(schema={"timestamp": pl.Utf8, "source": pl.Utf8, "type": pl.Utf8, "message": pl.Utf8})
        error_logs_raw = await redis_client.lrange("system_errors", 0, -1)
        if not error_logs_raw:
            return pl.DataFrame(schema={"timestamp": pl.Utf8, "source": pl.Utf8, "type": pl.Utf8, "message": pl.Utf8})
        error_logs = [json.loads(log) for log in error_logs_raw]
        df = pl.from_records(error_logs)
        return df.sort("timestamp", descending=True)
    except Exception as e:
        return pl.DataFrame([{"timestamp": datetime.now(timezone.utc).isoformat(), "source": "Dashboard", "type": "Error", "message": f"Failed to load errors: {e}"}])

def load_backtest_log(pair: str, strategy: str) -> pl.DataFrame:
    """バックテストのCSVログを読み込む（エラーログ追加）"""
    try:
        date_str = datetime.now(timezone.utc).strftime('%Y%m%d')
        data_dir = cfg.project_root / cfg.get_sync("system.data_dir", "data")
        fname = data_dir / f"backtest_{pair}_{strategy}_{date_str}.csv"
        
        if not fname.exists():
            yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime('%Y%m%d')
            fname = data_dir / f"backtest_{pair}_{strategy}_{yesterday_str}.csv"
            if not fname.exists():
                logger.debug(f"バックテストログが存在しません: {fname}")
                return pl.DataFrame()
                
        with open(fname, 'r', encoding='utf-8') as f:
            f.readline() # ヘッダー読み飛ばし等の調整が必要な場合
            df = pl.read_csv(f)
        return df.with_columns(pl.col("Entry Time").str.to_datetime(strict=False))
    except Exception as e:
        logger.error(f"load_backtest_log でエラー発生: {e}\n{traceback.format_exc()}")
        return pl.DataFrame()

def load_order_log(date_str: str) -> pl.DataFrame:
    """リアルタイム注文ログを読み込む"""
    try:
        fname = os.path.join(cfg.project_root, cfg.get_sync("system.data_dir", "data"), f"order_log_{date_str}.csv")
        if not os.path.exists(fname): return pl.DataFrame()
        return pl.read_csv(fname).with_columns(pl.col("ts").str.to_datetime(strict=False))
    except Exception:
        return pl.DataFrame()

def load_benchmark_data(date_str: str) -> pl.DataFrame:
    """ベンチマークログを読み込む"""
    try:
        fname = os.path.join(cfg.project_root, cfg.get_sync("system.log_dir", "logs"), f"benchmark_{date_str}.csv")
        if not os.path.exists(fname): return pl.DataFrame()
        return pl.read_csv(fname).with_columns(pl.col("timestamp").str.to_datetime(strict=False))
    except Exception:
        return pl.DataFrame()

@st.cache_data(ttl=60, show_spinner=False)
def load_m15_data(pair: str) -> pl.DataFrame:
    """M15の価格データをParquetファイルから読み込む（年別ファイル対応・エラーログ追加）"""
    try:
        # 💡 スクリプトがあるFXsysフォルダを基準に、絶対パスでdataディレクトリを確実に指し示す
        current_fxsys_dir = Path(__file__).resolve().parent
        pair_dir = current_fxsys_dir / "data" / pair
        if not pair_dir.exists():
            logger.warning(f"[{pair}] データディレクトリが存在しません: {pair_dir}")
            return pl.DataFrame()
            
        # 年別に分割されたParquetファイルをすべて取得して結合
        parquet_files = sorted(list(pair_dir.glob("*.parquet")))
        if not parquet_files:
            logger.warning(f"[{pair}] Parquetファイルが見つかりません: {pair_dir}")
            return pl.DataFrame()
            
        dfs = [pl.read_parquet(f) for f in parquet_files]
        df = pl.concat(dfs)
        
        if "time" in df.columns and df["time"].dtype != pl.Datetime:
            df = df.with_columns(pl.col("time").str.to_datetime(strict=False))
        return df.sort("time")
    except Exception as e:
        logger.error(f"[{pair}] load_m15_data でエラー発生: {e}\n{traceback.format_exc()}")
        return pl.DataFrame()

def load_trend_strength(date_str: str) -> pl.DataFrame:
    """トレードログからトレンド強度のデータを読み込む"""
    try:
        fname = os.path.join(cfg.project_root, cfg.get_sync("system.log_dir", "logs"), f"trade_log_{date_str}.csv")
        if not os.path.exists(fname): return pl.DataFrame()
        return pl.read_csv(fname).with_columns(pl.col("ts").str.to_datetime(strict=False))
    except Exception:
        return pl.DataFrame()

def load_trade_logs(date_str: str) -> pl.DataFrame:
    """トレードログを読み込む（エラーログ追加）"""
    try:
        fname = cfg.project_root / cfg.get_sync("system.log_dir", "logs") / f"trade_log_{date_str}.csv"
        if not fname.exists():
            logger.debug(f"トレードログが存在しません: {fname}")
            return pl.DataFrame()
            
        return pl.read_csv(fname).with_columns(pl.col("timestamp").str.to_datetime(strict=False))
    except Exception as e:
        logger.error(f"load_trade_logs でエラー発生: {e}\n{traceback.format_exc()}")
        return pl.DataFrame()

def load_virtual_trade_logs() -> pl.DataFrame:
    """仮想取引ログを読み込む（エラーログ追加）"""
    try:
        log_dir = Path(cfg.project_root) / cfg.get_sync("system.log_dir", "logs")
        log_files = sorted(list(log_dir.glob("virtual_trade_log_*.csv")))
        
        if not log_files:
            logger.debug(f"仮想取引ログが存在しません: {log_dir}")
            return pl.DataFrame()
        
        dfs = [pl.read_csv(f) for f in log_files]
        df = pl.concat(dfs)
        # 💡 Polarsの最新仕様に合わせ、タイムゾーンを明示してパースする
        return df.with_columns(pl.col("timestamp").str.to_datetime(time_zone="UTC", strict=False)).sort("timestamp")
    except Exception as e:
        logger.error(f"load_virtual_trade_logs でエラー発生: {e}\n{traceback.format_exc()}")
        return pl.DataFrame()

async def load_m5_atr_data(pair: str) -> tuple[list, list, float]:
    """RedisからM5のATR時系列データを取得する"""
    try:
        rolling = Rolling(size=720)
        redis = await get_redis(cfg, logger)
        redis_key = f"atr_m5_hist:{pair}"
        redis_data = await redis.lrange(redis_key, -720, -1)
        atr_values = [float(x) for x in redis_data] if redis_data else [0.0] * 720
        for value in atr_values: rolling.push(value)
        times = [datetime.now(timezone.utc) - timedelta(minutes=5 * i) for i in range(len(atr_values) - 1, -1, -1)]
        mean_atr = rolling.mean()
        threshold = mean_atr * cfg.get_sync("risk_management.atr_block_threshold", 2.0)
        return times, atr_values, threshold
    except Exception as e:
        await log_error(logger, "load_m5_atr_data", error=e)
        return [], [], 0.0

def calculate_kpis(df: pl.DataFrame, data_source: str) -> dict:
    """DataFrameから主要業績評価指標（KPI）を計算する"""
    # ... (関数の内容は変更なし)
    initial_balance = cfg.get_sync("trading.initial_balance", 1_000_000)
    if df.is_empty(): return {"equity": initial_balance, "win_rate": 0.0, "profit_factor": 0.0, "sharpe_ratio": 0.0, "max_drawdown": 0.0, "total_trades": 0}
    profit_col = "PNL" if data_source == "backtest" and "PNL" in df.columns else "pnl"
    if profit_col not in df.columns: return {"equity": initial_balance, "win_rate": 0.0, "profit_factor": 0.0, "sharpe_ratio": 0.0, "max_drawdown": 0.0, "total_trades": 0}
    equity = df[profit_col].cum_sum() + initial_balance
    wins = df.filter(pl.col(profit_col) > 0).shape[0]
    total_trades = df.shape[0]
    drawdown = abs(((equity - equity.cum_max()) / equity.cum_max()).min() * 100) if not equity.is_empty() else 0.0
    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0.0
    profit_sum = df.filter(pl.col(profit_col) > 0)[profit_col].sum() or 0
    loss_sum = df.filter(pl.col(profit_col) < 0)[profit_col].abs().sum() or 0
    profit_factor = profit_sum / loss_sum if loss_sum > 0 else float('inf')
    returns_col = "Return" if data_source == "backtest" and "Return" in df.columns else profit_col
    returns = df[returns_col].to_numpy()
    sharpe_ratio = (returns.mean() / (returns.std() + 1e-9)) * np.sqrt(252 * (24*4)) if returns.std() > 0 else 0.0
    return {"equity": float(equity[-1]), "win_rate": float(win_rate), "profit_factor": float(profit_factor), "sharpe_ratio": float(sharpe_ratio), "max_drawdown": float(drawdown), "total_trades": total_trades}

async def fetch_open_positions() -> list:
    """現在保有中のポジション情報を取得する"""
    # ... (関数の内容は変更なし)
    try:
        executor = await get_executor()
        return executor.get_open_trades()
    except Exception:
        return []
    
async def fetch_account_summary() -> dict:
    """OANDAから口座サマリーを取得する"""
    # ... (関数の内容は変更なし)
    try:
        executor = await get_executor()
        return await executor.get_account_summary()
    except Exception:
        return {}
    
async def load_system_logs() -> list:
    """システムログ（app_...jsonlog）を読み込む"""
    # ... (関数の内容は変更なし)
    try:
        redis_client = await get_redis(cfg, logger)
        if not redis_client: raise ConnectionError("Redis is not available.")
        cache_key = "log_cache:system"
        if (cached_logs := await redis_client.get(cache_key)) is not None:
            return json.loads(cached_logs)
        log_path = os.path.join(cfg.project_root, cfg.get_sync("system.log_dir", "logs"), f"app_{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonlog")
        if not os.path.exists(log_path): return []
        with open(log_path, "r", encoding="utf-8") as f:
            logs = [json.loads(line.strip()) for line in f if line.strip()]
        await redis_client.setex(cache_key, 5, json.dumps(logs))
        return logs
    except Exception:
        return []

async def fetch_signal_candidates() -> list:
    """Redisからシグナル候補を取得する"""
    try:
        redis_client = await get_redis(cfg, logger)
        if not redis_client: return []
        candidates_raw = await redis_client.hgetall("signal_candidates")
        return [json.loads(c) for c in candidates_raw.values()]
    except Exception:
        return []

@st.cache_data(ttl=60, show_spinner=False)
def load_optimization_results() -> pd.DataFrame:
    """最適化結果のログファイルを読み込む"""
    data_dir = Path(__file__).resolve().parent / "data"
    results = []
    if not data_dir.exists(): return pd.DataFrame()
    rejected_log_path = data_dir / "rejected_proposals.log"
    rejected_ids = set()
    if rejected_log_path.exists():
        with open(rejected_log_path, 'r', encoding='utf-8') as f:
            rejected_ids = set(line.strip() for line in f)
    for f in data_dir.glob("best_params_*.json"):
        try:
            unique_id = f"{f.stem}_{int(f.stat().st_mtime)}"
            if unique_id in rejected_ids: continue
            parts = f.stem.split('_')
            pair, strategy = f"{parts[2]}_{parts[3]}", parts[4]
            with open(f, 'r', encoding='utf-8') as file:
                params = json.load(file)
            results.append({"id": unique_id, "通貨ペア": pair, "戦略": strategy, "最適化日時": datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'), "最適パラメータ": json.dumps(params, ensure_ascii=False), "_params_dict": params})
        except Exception:
            continue
    if not results: return pd.DataFrame()
    df = pd.DataFrame(results).sort_values(by="最適化日時", ascending=False)
    # 最新のものだけを残す（古いものはUI上から表示を消去）
    df = df.drop_duplicates(subset=["通貨ペア", "戦略"], keep="first")
    return df

from functools import lru_cache
import asyncio

@lru_cache(maxsize=256)
def _cached_translate(text: str) -> str:
    """翻訳結果をメモリに記憶する（同期関数）"""
    if not text: return ""
    try:
        from deep_translator import GoogleTranslator
        return GoogleTranslator(source='en', target='ja').translate(text)
    except Exception:
        return text

async def load_news_and_indicators() -> list:
    """経済ニュースと指標をRedisから取得し、非同期で日本語に翻訳する"""
    try:
        redis_client = await get_redis(cfg, logger)
        if not redis_client: return []
        news_raw = await redis_client.get("news_indicators")
        if not news_raw: return []
        news_data = json.loads(news_raw)
        
        # 💡 同期的な翻訳処理を別スレッドで一括実行するための内部関数
        def _translate_all():
            translated_news = []
            for item in news_data:
                translated_item = item.copy()
                if "event" in translated_item: 
                    translated_item["event"] = _cached_translate(translated_item["event"])
                if "description" in translated_item: 
                    translated_item["description"] = _cached_translate(translated_item["description"])
                translated_news.append(translated_item)
            return translated_news

        # 💡 魔法の1行：メインスレッド（UIや他のデータ取得）を止めずに、裏の別スレッドで翻訳させる
        return await asyncio.to_thread(_translate_all)
        
    except Exception as e:
        await log_error(logger, "load_news_and_indicators", error=e)
        return []

async def load_economic_calendar() -> list:
    """Redisから経済指標カレンダーを取得し、リスト形式で返す。"""
    try:
        redis_client = await get_redis(cfg, logger)
        if not redis_client:
            return []
            
        # 今日の週初め（月曜日）を計算
        now = datetime.now(timezone.utc)
        start_of_week = now - timedelta(days=now.weekday())
        # 週次キャッシュキー
        cache_key = "economic_calendar:" + start_of_week.strftime('%Y-W%V')
        cached_data = await redis_client.get(cache_key)

        if not cached_data:
            return []

        # JSON文字列をPythonのリストに変換して返す
        events = json.loads(cached_data)
        return events
    except Exception as e:
        await log_error(logger, "load_economic_calendar", error=e)
        return []

async def check_system_health() -> dict:
    """システムのヘルス状態をチェックする"""
    health = {"oanda": False, "redis": False}
    try:
        executor = await get_executor()
        if executor: health["oanda"] = True
    except Exception:
        pass
    try:
        redis_client = await get_redis(cfg, logger)
        if redis_client and await redis_client.ping():
            health["redis"] = True
    except Exception:
        pass
    return health

async def execute_trade_close(trade_id: str, units: int) -> bool:
    """特定のポジションを強制決済する"""
    try:
        executor = await get_executor()
        await executor.partial_close_position(trade_id, units)
        return True
    except Exception as e:
        await log_error(logger, "execute_trade_close", error=e)
        return False

async def execute_close_all() -> bool:
    """全ポジションを強制決済する"""
    try:
        executor = await get_executor()
        trades_dict = executor.get_all_open_trades()
        for pair, trades in trades_dict.items():
            for trade in trades:
                await executor.partial_close_position(trade["id"], trade["current_units"])
        return True
    except Exception as e:
        await log_error(logger, "execute_close_all", error=e)
        return False

import threading
import subprocess

def _get_sync_redis_client():
    import redis
    # 💡 localhostを明示的に127.0.0.1にし、タイムアウトを設定
    redis_url = cfg.get_sync("system.redis_url", "redis://127.0.0.1:6379/1")
    redis_url = redis_url.replace("localhost", "127.0.0.1")
    return redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=1.5,  # 追加
        socket_timeout=1.5           # 追加
    )

def _run_cmd_in_background(cmd_list: list, job_key: str):
    """サブプロセスを実行し、Redisでステータスを管理するバックグラウンドワーカー"""
    r = _get_sync_redis_client()
    try:
        r.set(f"job_status:{job_key}", "running")
        # 🚨 追加: ジョブの開始時刻を記録（将来的なゾンビプロセスのタイムアウト判定用）
        r.set(f"job_start_time:{job_key}", datetime.now(timezone.utc).isoformat())
        logger.info(f"Starting background job [{job_key}]: {' '.join(cmd_list)}")
        
        import subprocess
        # 🚨 変更: stdout と stderr をキャプチャし、エラー終了時(check=True)に例外を発生させる
        result = subprocess.run(cmd_list, capture_output=True, text=True, check=True)
        
        logger.info(f"Background job [{job_key}] completed successfully.\nOutput: {result.stdout}")
    except subprocess.CalledProcessError as e:
        # 🚨 追加: 実行したスクリプトがエラーを吐いて異常終了した場合の詳細なログを記録
        logger.error(f"Background job [{job_key}] failed with exit code {e.returncode}.\nError Output: {e.stderr}")
    except Exception as e:
        logger.error(f"Background job [{job_key}] encountered an unexpected exception: {e}")
    finally:
        try:
            r.set(f"job_status:{job_key}", "completed")
            r.delete(f"job_start_time:{job_key}")
        except Exception:
            pass
        finally:
            r.close()

def start_backtest_job(start_date: str = None, end_date: str = None) -> bool:
    """バックテストを非同期で開始する（期間は自動設定）"""
    r = _get_sync_redis_client()
    try:
        if r.get("job_status:backtest") == "running":
            return False
    except Exception as e:
        logger.error(f"Redis connection failed in start_backtest_job: {e}")
        return False
    finally:
        r.close()
    
    import threading
    from pathlib import Path
    import sys
    venv_python = sys.executable
    current_dir = Path(__file__).resolve().parent
    
    # 複数コマンドを順次実行し、出力をリダイレクトするためのPythonスクリプトを生成
    py_script = f"""
import subprocess, sys, asyncio
from pathlib import Path

# パスを追加してモジュールを正しく読み込めるようにする
sys.path.insert(0, '{current_dir.parent.as_posix()}')
from core.notify import notify_discord
from core.config_manager import ConfigManager
cfg_obj = ConfigManager(Path('{current_dir.parent.as_posix()}'))

try:
    asyncio.run(notify_discord(cfg_obj, "🚀 **【R&Dバックテスト開始】**\\n手動での長期バックテスト(OHLC Replay)が開始されました。", "system"))
    
    # 過去のバックテスト残骸を削除（リセット）
    decision_log = Path('{current_dir.as_posix()}/data/reports/decision_log_multi_pairs.csv')
    if decision_log.exists():
        decision_log.unlink()
        
    rnd_report = Path('{current_dir.as_posix()}/data/rnd_latest_analysis.txt')
    if rnd_report.exists():
        rnd_report.unlink()
        
    # Redisのフェーズ・進捗をリセット
    try:
        import redis
        r = redis.from_url(cfg_obj.get_sync("system.redis_url", "redis://127.0.0.1:6379/1").replace("localhost", "127.0.0.1"))
        r.delete("job_phase:backtest")
        r.delete("job_progress:backtest")
        r.close()
    except:
        pass
        
    print('1. Running replay_backtester.py...')
    cmd1 = [sys.executable, '{current_dir.as_posix()}/replay_backtester.py']
"""
    if start_date and end_date:
        py_script += f"    cmd1.extend(['--start', '{start_date}', '--end', '{end_date}'])\n"
        
    py_script += f"""
    subprocess.run(cmd1, check=True)
    
    print('2. Running analyze_comprehensive.py...')
    out_file = Path('{current_dir.as_posix()}/data/rnd_latest_analysis.txt')
    out_file.parent.mkdir(exist_ok=True)
    with open(out_file, 'w', encoding='utf-8') as f:
        subprocess.run([sys.executable, '{current_dir.as_posix()}/analyze_comprehensive.py', '--csv', '{current_dir.as_posix()}/data/reports/decision_log_multi_pairs.csv'], stdout=f, check=True)
    print('All R&D backtest processes completed successfully.')
    asyncio.run(notify_discord(cfg_obj, "✅ **【R&Dバックテスト完了】**\\nバックテストとデータ集計が完了しました。ダッシュボードから結果確認とAI分析を実行できます。", "system"))
except Exception as e:
    print(f'Error during R&D backtest process: {{e}}')
    try:
        asyncio.run(notify_discord(cfg_obj, f"❌ **【R&Dバックテスト失敗】**\\nエラーが発生しました: {{e}}", "system"))
    except:
        pass
    sys.exit(1)
"""
    cmd = [venv_python, "-c", py_script]
    t = threading.Thread(target=_run_cmd_in_background, args=(cmd, "backtest"), daemon=True)
    t.start()
    return True

def start_wfa_job(months: int = None) -> bool:
    """週次4段階パイプライン（WFA連鎖）を非同期で開始する（期間・設定は自動算出）"""
    r = _get_sync_redis_client()
    try:
        if r.get("job_status:wfa") == "running":
            return False
    except Exception as e:
        logger.error(f"Redis connection failed in start_wfa_job: {e}")
        return False
    finally:
        r.close()
    
    import threading
    from pathlib import Path
    import sys
    venv_python = sys.executable
    current_dir = Path(__file__).resolve().parent
    # scheduler.pyの週次4段階パイプライン（run_weekly_pipeline）を完全実行
    cmd = [venv_python, "-c", "import asyncio, sys; sys.path.insert(0, '.'); import scheduler; asyncio.run(scheduler.run_weekly_pipeline())"]
    t = threading.Thread(target=_run_cmd_in_background, args=(cmd, "wfa"), daemon=True)
    t.start()
    return True

async def get_job_statuses_async() -> dict:
    """現在のジョブ実行状況を非同期でRedisおよびローカル状態から事前取得する"""
    try:
        from core.status_manager import get_pipeline_status
        pipe_state = await get_pipeline_status(cfg)
        
        redis_client = await get_redis(cfg, logger)
        bt_running = False
        bt_progress = 0.0
        bt_phase = ""
        if redis_client:
            bt_status = await redis_client.get("job_status:backtest")
            bt_running = (bt_status == "running")
            _prog = await redis_client.get("job_progress:backtest")
            bt_progress = float(_prog) if _prog else 0.0
            bt_phase = await redis_client.get("job_phase:backtest") or ""
            await redis_client.close()

        wfa_running = pipe_state.get("is_running", False) or (pipe_state.get("status") == "running")

        return {
            "backtest": bt_running,
            "backtest_progress": bt_progress,
            "backtest_phase": bt_phase,
            "wfa": wfa_running,
            "pipeline_phase": pipe_state.get("phase", ""),
            "pipeline_progress": pipe_state.get("progress", 0.0),
            "pipeline_status": pipe_state.get("status", "idle"),
            "pipeline_updated_at": pipe_state.get("updated_at", None),
            "pipeline_details": pipe_state.get("details", {})
        }
    except Exception as e:
        logger.error(f"Failed to get job statuses: {e}")
        return {
            "backtest": False, 
            "wfa": False,
            "pipeline_phase": "",
            "pipeline_progress": 0.0,
            "pipeline_status": "idle"
        }

# --- キャッシュ付きシステムヘルスチェック（FUTURE-03 タスク4） ---
_health_cache = {"data": None, "last_check": 0}

async def check_system_health_cached(ttl_seconds: int = 30) -> dict:
    """システムヘルス（Redis/OANDA）をキャッシュ付きでチェックする"""
    global _health_cache
    now = time.time()
    
    # TTL内ならキャッシュを返す
    if _health_cache["data"] and (now - _health_cache["last_check"] < ttl_seconds):
        return _health_cache["data"]

    health = {"oanda": False, "oanda_latency_ms": 0, "redis": False}

    # 1. Redis Ping
    try:
        redis_client = await get_redis(cfg, logger)
        if redis_client and await redis_client.ping():
            health["redis"] = True
    except Exception:
        health["redis"] = False

    # 2. OANDA API Ping (レイテンシ計測込み)
    try:
        start_t = time.perf_counter()
        executor = await get_executor()
        summary = await executor.get_account_summary()
        if summary:
            health["oanda_latency_ms"] = int((time.perf_counter() - start_t) * 1000)
            health["oanda"] = True
    except Exception:
        health["oanda"] = False

    _health_cache["data"] = health
    _health_cache["last_check"] = now
    return health
def archive_and_reset_virtual_forward() -> bool:
    """現在の仮想フォワード履歴をアーカイブ用CSVに追記し、アクティブなログをリセットする"""
    try:
        log_dir = cfg.project_root / cfg.get_sync("system.log_dir", "logs")
        reports_dir = cfg.project_root / "data" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        archive_file = reports_dir / "virtual_forward_history.csv"
        
        # 1. 既存のCSVを結合
        csv_files = sorted(list(log_dir.glob("virtual_trade_log_*.csv")))
        if csv_files:
            dfs = []
            for f in csv_files:
                try:
                    df = pd.read_csv(f)
                    if not df.empty:
                        dfs.append(df)
                except Exception as e:
                    logger.warning(f"Failed to read {f}: {e}")
            
            if dfs:
                combined_df = pd.concat(dfs, ignore_index=True)
                # 追記か新規作成か
                if archive_file.exists():
                    try:
                        existing_df = pd.read_csv(archive_file)
                        combined_df = pd.concat([existing_df, combined_df], ignore_index=True)
                        # 重複排除 (transaction_idがある場合など、完全一致行は消す)
                        combined_df = combined_df.drop_duplicates()
                    except Exception as e:
                        logger.error(f"Failed to read existing archive {archive_file}: {e}")
                
                # アーカイブ保存
                combined_df.to_csv(archive_file, index=False, encoding="utf-8")
                logger.info(f"Successfully archived {len(dfs)} log files to {archive_file}")

        # 2. リセット処理
        for f in csv_files:
            try:
                f.unlink()
            except Exception as e:
                logger.warning(f"Could not delete {f}: {e}")
                
        # 3. HWMとOpen Positionのリセット
        hwm_file = cfg.project_root / "data" / "hwm_state.json"
        pos_file = cfg.project_root / "data" / "virtual_open_positions.json"
        
        with open(pos_file, "w", encoding="utf-8") as f:
            f.write("{}")
            
        with open(hwm_file, "w", encoding="utf-8") as f:
            json.dump({"hwm": 1000000.0, "current_balance": 1000000.0, "last_updated": ""}, f)
            
        logger.info("Virtual forward data reset completed successfully.")
        return True
    except Exception as e:
        logger.error(f"Error in archive_and_reset_virtual_forward: {e}\n{traceback.format_exc()}")
        return False
