# replay_backtester.py - OHLC 軽量リプレイバックテスター (v21 - TSL Fully Integrated)
import asyncio
import sys
import pandas as pd
import polars as pl
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List

from core import cfg
from core.logger import get_logger
from core.virtual_trade import VirtualBroker
from core.signal_module import SignalGenerator
from core.indicators import add_all_indicators
from core.data import fetch_candles

import virtual_main_loop
import core.virtual_trade
import core.monitor

logger = get_logger(cfg, "ohlc_replay_tester")

# 時間のモック化
_current_sim_time = None
class FakeDatetime:
    @classmethod
    def now(cls, tz=None):
        return _current_sim_time

virtual_main_loop.datetime = FakeDatetime
core.virtual_trade.datetime = FakeDatetime
core.monitor.datetime = FakeDatetime

class DummyExecutor:
    def __init__(self, broker: VirtualBroker, current_pair: str): 
        self.broker = broker
        self.current_pair = current_pair
    async def get_account_summary(self): return {"balance": self.broker.balance}
    def get_open_trades(self, p): return [t for t in self.broker.positions if t.get('pair') == p]
    def get_all_open_trades(self): return {self.current_pair: self.broker.positions}
    async def execute_trade(self, signal):
        # 【修正】signalから時刻を取り出して、current_timeとしてブローカーに渡す
        sig_time = signal.get("time", _current_sim_time)
        self.broker.open_position(
            pair=signal["pair"], direction=signal["signal"], lot_size=signal["units"],
            entry_price=signal["entry_price"], sl_price=signal["sl"], tp_price=signal["tp"], 
            context=signal, current_time=sig_time # 👈 ここを追加
        )
    async def update_trade_stop_loss(self, trade_id: str, new_sl_price: float):
        for pos in self.broker.positions:
            if pos["id"] == trade_id:
                pos["sl_price"] = new_sl_price
                break

class OHLCReplayJob:
    def __init__(self, pair: str, start_date: str, end_date: str, initial_balance: float = 1_000_000.0):
        self.pair = pair
        self.start_date_str = start_date
        self.end_date_str = end_date
        
        self.broker = VirtualBroker(initial_balance=initial_balance)
        self.broker._monitor_kill_switch = lambda *args, **kwargs: None
        
        self.executor = DummyExecutor(self.broker, self.pair)
        self.signal_gen = SignalGenerator(executor=self.executor)
        
        self.trade_stats = []
        self.decision_logs = []

    async def run(self):
        global _current_sim_time
        logger.info(f"[{self.pair}] Loading OHLC data from Parquet ({self.start_date_str} to {self.end_date_str})...")
        
        df_m5 = await fetch_candles(pair=self.pair, start=self.start_date_str, end=self.end_date_str)
        if df_m5 is None or df_m5.is_empty():
            logger.error(f"[{self.pair}] No data found for the specified period.")
            return

        wfa_params = self.signal_gen._get_latest_wfa_params(self.pair, "trend")
        logger.info(f"[{self.pair}] Calculating indicators with WFA params: {wfa_params}")
        df_m5_features = await add_all_indicators(df_m5, wfa_params)
        
        logger.info(f"[{self.pair}] Starting OHLC Replay Simulation...")
        sim_df = df_m5_features.filter(
            (pl.col("time") >= pd.to_datetime(self.start_date_str, utc=True)) & 
            (pl.col("time") <= pd.to_datetime(self.end_date_str, utc=True))
        )
        
        for i, row in enumerate(sim_df.iter_rows(named=True)):
            _current_sim_time = row["time"]
            
            # ＝＝＝ ▼ replay_backtester.py の process_tick 関数を上書き ▼ ＝＝＝
            async def process_tick(price: float):
                # 1. 保有ポジションに対して「本番用TSL」を評価し、必要ならSLを引き上げる（最強の矛）
                for p in self.broker.positions:
                    if p["pair"] == self.pair:
                        await self.signal_gen._check_and_execute_tsl(p, price)
                # 2. ブローカー側で更新されたSL（またはTP）へのヒット判定を実行する（最強の盾）
                # 【修正】シミュレーション時刻（_current_sim_time）を渡す
                await self.broker.update_market_price(self.pair, price, price, current_time=_current_sim_time)
            # ＝＝＝ ▲ 修正 ここまで ▲ ＝＝＝

            # --- ブローカーの価格更新（建玉のSL/TP判定） ---
            open_positions = [p for p in self.broker.positions if p["pair"] == self.pair]
            if open_positions:
                direction = open_positions[0]["direction"]
                # 🛡️ 悲観的モデル（保守的評価）
                if direction == "long":
                    # ロングは先に安値(Low)を評価してSL落ちを確認、生き残れば高値(High)でTSL更新
                    await process_tick(row["low"])
                    await process_tick(row["high"])
                else:
                    # ショートは先に高値(High)を評価してSL落ちを確認、生き残れば安値(Low)でTSL更新
                    await process_tick(row["high"])
                    await process_tick(row["low"])
                await process_tick(row["close"])
            else:
                await process_tick(row["close"])

            # 決済されたポジションの記録
            closed = [p for p in self.broker.positions if p.get('status') == 'closed']
            for c in closed:
                self.trade_stats.append(c)
                self.broker.positions.remove(c)

            # --- シグナル生成とエグジット評価（本番モジュールの呼び出し） ---
            if i >= 300:
                history_df = sim_df.slice(i - 300, 301)
                history_pd = history_df.to_pandas()
                
                open_positions = [p for p in self.broker.positions if p["pair"] == self.pair]
                exit_commands = self.signal_gen.check_signal_exits(self.pair, history_pd, open_positions)
                
                for cmd in exit_commands:
                    await self.broker.close_position(cmd["id"], cmd["reason"])
                    logger.info(f"[{self.pair}] ⚡ 即時撤退発動: {cmd['reason']}")
                
                signal = await self.signal_gen.generate_signal(
                    pair=self.pair, 
                    df_m5=None, 
                    _precomputed_features=history_df, 
                    sentiment_score=0.0
                )
                
                if signal:
                    await self.executor.execute_trade(signal)
                    self.decision_logs.append({
                        "Time": _current_sim_time.strftime("%Y-%m-%d %H:%M"),
                        "Pair": self.pair, "Action": "ENTRY", "Direction": signal["signal"],
                        "Price": row["close"], "SL": signal.get("sl"), "TP": signal.get("tp")
                    })

        logger.info(f"[{self.pair}] Simulation Completed. Remaining positions: {len(self.broker.positions)}")
        
        wins = [t for t in self.trade_stats if t["profit_jpy"] > 0]
        gross_profit = sum(t["profit_jpy"] for t in wins)
        gross_loss = abs(sum(t["profit_jpy"] for t in self.trade_stats if t["profit_jpy"] <= 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else 0
        win_rate = len(wins) / len(self.trade_stats) * 100 if self.trade_stats else 0
        
        logger.info("\n" + "="*50)
        logger.info(f" 🏆 【{self.pair}】 OHLC Replay 結果")
        logger.info(f" 最終残高 : {self.broker.balance:,.0f} JPY")
        logger.info(f" 取引回数 : {len(self.trade_stats)} 回 (勝率: {win_rate:.1f}%)")
        logger.info(f" PF       : {pf:.2f}")
        logger.info("="*50 + "\n")
        
        return self.broker.balance, self.broker.positions, self.trade_stats, self.decision_logs

async def main():
    SIM_START_DATE = "2026-01-01 00:00:00"
    SIM_END_DATE   = "2026-06-01 00:00:00"
    TARGET_PAIRS   = ["USD_JPY", "EUR_JPY", "GBP_JPY"]
    
    report_dir = Path("data/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "decision_log_multi_pairs.csv"
    
    if report_path.exists():
        try:
            report_path.unlink()
        except Exception:
            pass

    for pair in TARGET_PAIRS:
        job = OHLCReplayJob(pair, SIM_START_DATE, SIM_END_DATE)
        balance, positions, stats, logs = await job.run()

        if logs:
            df_log = pd.DataFrame(logs)
            write_mode = 'a' if report_path.exists() else 'w'
            header = not report_path.exists()
            df_log.to_csv(report_path, mode=write_mode, header=header, index=False, encoding="utf-8-sig")
            logger.info(f"✅ {pair}のログをCSVに保存し、ダッシュボードに反映しました ({len(df_log)}件)")
        else:
            logger.warning(f"⚠️ {pair}の取引ログが0件だったため、CSVへの保存をスキップしました。")

async def main(start_date: str, end_date: str):
    SIM_START_DATE = start_date
    SIM_END_DATE   = end_date
    TARGET_PAIRS   = ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY"]
    
    report_dir = Path("data/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "decision_log_multi_pairs.csv"
    
    if report_path.exists():
        try:
            report_path.unlink()
        except Exception:
            pass

    for pair in TARGET_PAIRS:
        job = OHLCReplayJob(pair, SIM_START_DATE, SIM_END_DATE)
        balance, positions, stats, logs = await job.run()

        if logs:
            df_log = pd.DataFrame(logs)
            write_mode = 'a' if report_path.exists() else 'w'
            header = not report_path.exists()
            df_log.to_csv(report_path, mode=write_mode, header=header, index=False, encoding="utf-8-sig")
            logger.info(f"✅ {pair}のログをCSVに保存し、ダッシュボードに反映しました ({len(df_log)}件)")
        else:
            logger.warning(f"⚠️ {pair}の取引ログが0件だったため、CSVへの保存をスキップしました。")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="OHLC Replay Backtester")
    parser.add_argument("--start", type=str, default="2025-07-01 00:00:00", help="開始日時")
    parser.add_argument("--end", type=str, default="2026-06-30 00:00:00", help="終了日時")
    args = parser.parse_args()

    # Python 3.14以降で非推奨となる SelectorEventLoopPolicy は不要なため削除
    # asyncio.run でデフォルトのイベントループを安全に起動
    asyncio.run(main(args.start, args.end))