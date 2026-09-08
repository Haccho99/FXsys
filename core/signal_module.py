# core/signal_module.py (v36 - BB Squeeze Integrated)
from __future__ import annotations
import polars as pl
import pandas as pd
import numpy as np
import asyncio
from typing import Dict, Optional, Tuple, TYPE_CHECKING
import json
from pathlib import Path
import sys
import math
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
import glob
import re

from core import cfg
from core.indicators import add_all_indicators
from core.risk import tp_sl, position_units, calculate_lot_multiplier_from_score, calculate_virtual_lot
from core.logger import get_logger, log_error
from core.monitor import analyze_sentiment_llm

logger = get_logger(cfg, "signal_module")

if TYPE_CHECKING:
    from core.trade import TradeExecutor

async def retry_async_call(func, *args, **kwargs):
    decorated_func = retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(2),
        retry=retry_if_exception_type(Exception)
    )(func)
    return await decorated_func(*args, **kwargs)

class SignalGenerator:
    def __init__(self, executor: "TradeExecutor"):
        self.executor = executor
        self.cfg = cfg 
        risk_cfg = self.cfg.get_sync("risk_management", {})
        
        self.max_positions_per_pair = risk_cfg.get("max_positions_per_pair", 1)
        self.max_total_positions = risk_cfg.get("max_total_positions", 2)
        
        self.tsl_enabled = risk_cfg.get("enable_trailing_stop", False)
        self.tsl_activation_pct = risk_cfg.get("tsl_activation_pct", 0.5)
        
        self._trade_locks: Dict[str, asyncio.Lock] = {}
        self.score_entry_threshold = cfg.get_sync("ai.score_threshold", 0.55)
        logger.info("SignalGenerator initialized with position limits.", per_pair=self.max_positions_per_pair, total=self.max_total_positions)

    def _has_breached_position_limits(self, pair: str) -> bool:
        open_trades_for_pair = self.executor.get_open_trades(pair)
        if len(open_trades_for_pair) >= self.max_positions_per_pair:
            return True
        all_open_trades = self.executor.get_all_open_trades()
        total_positions = sum(len(trades) for trades in all_open_trades.values())
        if total_positions >= self.max_total_positions:
            return True
        return False

    def _get_latest_wfa_params(self, pair: str, strategy: str = "trend") -> dict:
        """最新のWFA最適化パラメータJSONを動的に読み込む。"""
        try:
            import glob
            import json
            import os

            data_dir = self.cfg.project_root / self.cfg.get_sync("system.data_dir", "data")
            patterns = [
                str(data_dir / f"best_params_{pair}__walk*.json"),
                str(data_dir / f"best_params_{pair}_*.json"),
                str(data_dir / f"best_params_{pair}.json")
            ]

            files = []
            for p in patterns:
                files.extend(glob.glob(p))
            files = list(set(files))

            if not files:
                return {}

            # ファイルの更新日時（mtime）が最新のものを取得
            latest_file = max(files, key=os.path.getmtime)

            if latest_file:
                with open(latest_file, 'r', encoding='utf-8') as f:
                    params = json.load(f)
                    # 💡 指定された戦略(trend等)のパラメータを取り出す
                    selected_params = params.get(strategy, params) if strategy in params else params
                    
                    # 💡 ここにログ出力を追加して、コンソールに中身を表示させる
                    logger.info(
                        f"[{pair}] Loaded WFA params ({strategy}) from {os.path.basename(latest_file)}: "
                        f"EMA({selected_params.get('ema_fast_span')}/{selected_params.get('ema_slow_span')}), "
                        f"ADX_th={selected_params.get('adx_threshold')}, "
                        f"BB_std={selected_params.get('bb_std')}, "
                        f"SL_mult={selected_params.get('sl_atr_multiplier')}, "
                        f"TP_mult={selected_params.get('tp_atr_multiplier')}"
                    )
                    
                    return selected_params
        except Exception as e:
            logger.error(f"Error loading dynamic WFA params for {pair}: {e}")
        return {}

    def check_signal_exits(self, pair: str, df_pd: pd.DataFrame, open_positions: list) -> list:
        """
        ★ハイブリッド・エグジット戦略★
        含み益がTPの40%未満：戦略固有の防衛ロジックで即撤退（最強の盾）
        含み益がTPの40%以上：Signal Exitを無効化し、TSLに利益極大化を任せる（最強の矛）
        """
        exits = []
        if not open_positions or len(df_pd) < 3: return exits # 2本前まで見るため3以上に変更
            
        required_cols = ['hist_m15']
        if not all(col in df_pd.columns for col in required_cols): return exits

        # ＝＝＝ ▼ 修正：Wave Ended 2バー確認ルール ▼ ＝＝＝
        curr_hist = df_pd['hist_m15'].iloc[-1]
        prev1_hist = df_pd['hist_m15'].iloc[-2]
        prev2_hist = df_pd['hist_m15'].iloc[-3]
        # ＝＝＝ ▲ 修正 ここまで ▲ ＝＝＝

        curr_close = df_pd["close" if "close" in df_pd.columns else "Close"].iloc[-1]

        low_col = "low" if "low" in df_pd.columns else "Low"
        high_col = "high" if "high" in df_pd.columns else "High"
        # 列が存在しない場合のフェイルセーフとして終値を代入
        prev1_low = df_pd[low_col].iloc[-2] if low_col in df_pd.columns else curr_close
        prev1_high = df_pd[high_col].iloc[-2] if high_col in df_pd.columns else curr_close
        
        curr_time_dt = None
        if "time" in df_pd.columns:
            curr_time_dt = pd.to_datetime(df_pd["time"].iloc[-1], utc=True)
        
        for pos in open_positions:
            pos_id = pos.get("id")
            direction = pos.get("direction")
            entry_price = pos.get("entry_price", pos.get("price", curr_close))
            tp_price = pos.get("tp", 0.0)
            
            context = pos.get("context", {})
            activation_pct = context.get("tsl_activation_pct", 0.40)
            strategy = pos.get("strategy", context.get("strategy", "trend"))
            
            profit_dist = (curr_close - entry_price) if direction == "long" else (entry_price - curr_close)
            
            is_safe_zone = False
            if tp_price != 0.0:
                tp_dist = abs(tp_price - entry_price)
                if profit_dist >= (tp_dist * activation_pct):
                    is_safe_zone = True

            if is_safe_zone:
                continue

            if strategy == "trend":
                # 既存の含み益限定 Wave Ended ロジック
                if direction == "long" and curr_hist < prev1_hist and curr_close < prev1_low and profit_dist > 0:
                    exits.append({"id": pos_id, "reason": "Signal Exit (Wave Ended - Long)"})
                elif direction == "short" and curr_hist > prev1_hist and curr_close > prev1_high and profit_dist > 0:
                    exits.append({"id": pos_id, "reason": "Signal Exit (Wave Ended - Short)"})
            
            elif strategy == "bb_squeeze":
                wfa_params_sqz = self._get_latest_wfa_params(pair, "bb_squeeze")
                
                open_time_str = pos.get("open_time") or pos.get("openTime")
                if open_time_str and curr_time_dt is not None:
                    try:
                        open_time_dt = pd.to_datetime(open_time_str, utc=True)
                        elapsed_minutes = (curr_time_dt - open_time_dt).total_seconds() / 60.0
                        time_stop_minutes = wfa_params_sqz.get("time_stop_minutes", 30)
                        
                        if elapsed_minutes >= time_stop_minutes and profit_dist <= 0:
                            exits.append({"id": pos_id, "reason": f"Signal Exit (Time Stop {elapsed_minutes:.0f}m >= {time_stop_minutes}m)"})
                            continue
                    except Exception as e:
                        logger.error(f"Time Stop calculation failed: {e}")

                if "rsi" in df_pd.columns:
                    curr_rsi = df_pd["rsi"].iloc[-1]
                    prev_rsi = df_pd["rsi"].iloc[-2]
                    rsi_drop_th = 5.0
                    
                    if direction == "long" and (prev_rsi - curr_rsi) >= rsi_drop_th:
                        exits.append({"id": pos_id, "reason": f"Signal Exit (M5 RSI Drop {prev_rsi:.1f}->{curr_rsi:.1f} - Long)"})
                    elif direction == "short" and (curr_rsi - prev_rsi) >= rsi_drop_th:
                        exits.append({"id": pos_id, "reason": f"Signal Exit (M5 RSI Rise {prev_rsi:.1f}->{curr_rsi:.1f} - Short)"})

        return exits

    async def generate_signal(self, pair: str, df_m5: Optional[pl.DataFrame] = None, _precomputed_features: Optional[pl.DataFrame] = None, sentiment_score: float = 0.0, df_m15: Optional[pl.DataFrame] = None, **kwargs) -> Optional[Dict]:
        """▼▼▼ MTFハイブリッド（Trend & BB Squeeze 並列評価） ▼▼▼"""
        try:
            raw_input_df = df_m5 if df_m5 is not None else (df_m15 if df_m15 is not None else kwargs.get("df_input"))
            logger.info(f"[{pair}] [DEBUG-SIG] === Signal Evaluation Start ===")

            if self._has_breached_position_limits(pair): 
                logger.info(f"[{pair}] [DEBUG-SIG] Rejected: Position limits breached")
                return None 

            wfa_params_trend = self._get_latest_wfa_params(pair, "trend")
            wfa_params_sqz = self._get_latest_wfa_params(pair, "bb_squeeze")

            if _precomputed_features is not None:
                df_with_features = _precomputed_features
            else:
                if raw_input_df is None:
                    logger.error(f"[{pair}] [DEBUG-SIG] Rejected: No input DataFrame provided")
                    return None
                # 事前計算に Trend 用のパラメータを代表として渡す
                df_with_features = await add_all_indicators(raw_input_df, wfa_params_trend)
        
            if df_with_features is None or len(df_with_features) < 10:
                logger.info(f"[{pair}] [DEBUG-SIG] Rejected: Insufficient feature data rows ({len(df_with_features) if df_with_features is not None else 0})")
                return None

            df_pd = df_with_features.to_pandas()
            
            import pytz
            from datetime import datetime
            
            # UTCとして読み込み、確実に日本時間（JST）へ変換
            if "time" in df_pd.columns:
                current_time_dt = pd.to_datetime(df_pd["time"].iloc[-1], utc=True)
            elif "timestamp" in df_pd.columns:
                current_time_dt = pd.to_datetime(df_pd["timestamp"].iloc[-1], utc=True)
            else:
                current_time_dt = datetime.now(pytz.utc)

            current_time_dt = current_time_dt.astimezone(pytz.timezone('Asia/Tokyo'))
            current_time = current_time_dt.time()
            current_weekday = current_time_dt.weekday() # 0:月曜 ... 3:木曜, 4:金曜, 5:土曜, 6:日曜
            current_hour = current_time_dt.hour
            
            # 🛡️ 確実な設定読み込み（二重安全装置）
            system_cfg = self.cfg.get_sync("system", {})
            time_filters = system_cfg.get("time_filters", {})
            
            if not time_filters:
                try:
                    project_root = Path(__file__).resolve().parent.parent
                    config_path = project_root / 'config.json'
                    if config_path.exists():
                        with open(config_path, 'r', encoding='utf-8') as f:
                            raw_cfg = json.load(f)
                            time_filters = raw_cfg.get("system", {}).get("time_filters", {})
                            system_cfg = raw_cfg.get("system", {})
                except Exception as e:
                    logger.error(f"Failed to load config.json: {e}")

            # 🛡️ 1. グローバル時間帯フィルター & 曜日専用フィルター
            if time_filters:
                # 週末停止判定
                friday_stop_hour = time_filters.get("friday_stop_hour", 20)
                if current_weekday == 4 and current_hour >= friday_stop_hour:
                    logger.info(f"[{pair}] [DEBUG-SIG] Rejected: Friday Stop (Hour: {current_hour} >= {friday_stop_hour})")
                    return None
                if current_weekday in (5, 6):
                    logger.info(f"[{pair}] [DEBUG-SIG] Rejected: Weekend ({current_weekday})")
                    return None
                    
                # 【木曜日(weekday=3) 専用フィルター】
                thursday_filters = time_filters.get("thursday_filters", {})
                if current_weekday == 3 and thursday_filters.get("enabled", False):
                    forbidden_hours = thursday_filters.get("forbidden_hours", [])
                    if current_hour in forbidden_hours:
                        logger.info(f"[{pair}] [DEBUG-SIG] Rejected: Thursday forbidden hour ({current_hour})")
                        return None
                    
                    forbidden_pairs = thursday_filters.get("forbidden_pairs", [])
                    if pair in forbidden_pairs:
                        logger.info(f"[{pair}] [DEBUG-SIG] Rejected: Thursday forbidden pair ({pair})")
                        return None

                # 魔の時間帯 (forbidden_start ~ forbidden_end) 判定
                f_start_str = time_filters.get("forbidden_start")
                f_end_str = time_filters.get("forbidden_end")
                if f_start_str and f_end_str:
                    try:
                        f_start_t = datetime.strptime(f_start_str, "%H:%M").time()
                        f_end_t = datetime.strptime(f_end_str, "%H:%M").time()
                        
                        is_forbidden = False
                        if f_start_t <= f_end_t:
                            if f_start_t <= current_time <= f_end_t:
                                is_forbidden = True
                        else: # 日またぎ設定
                            if current_time >= f_start_t or current_time <= f_end_t:
                                is_forbidden = True
                                
                        if is_forbidden:
                            logger.info(f"[{pair}] [DEBUG-SIG] Rejected: Forbidden Time ({current_time} in {f_start_str}-{f_end_str})")
                            return None
                    except Exception as e:
                        logger.error(f"Time filter parsing error: {e}")

            # 🛡️ 2. ペア別 Active Windows判定
            active_windows = system_cfg.get("active_windows", {}).get(pair, [])
            is_in_window = False
            
            if not active_windows:
                is_in_window = True 
            else:
                for window in active_windows:
                    start_str = window.get("start")
                    end_str = window.get("end")
                    if not start_str or not end_str: continue
                    try:
                        start_t = datetime.strptime(start_str, "%H:%M").time()
                        end_t = datetime.strptime(end_str, "%H:%M").time()
                        if start_t <= end_t:
                            if start_t <= current_time <= end_t:
                                is_in_window = True; break
                        else:
                            if current_time >= start_t or current_time <= end_t:
                                is_in_window = True; break
                    except:
                        pass
                            
            if not is_in_window:
                logger.info(f"[{pair}] [DEBUG-SIG] Rejected: Outside Active Windows (Current JST: {current_time}, Windows: {active_windows})")
                return None

            # ---------------------------------------------------------
            # ロジック判定用データの抽出
            # ---------------------------------------------------------
            required_cols = [
                'close_h1', 'ema_fast_h1', 'ema_slow_h1', 'adx_m15', 'hist_m15', 'hist_m15_prev',
                'bb_squeeze_ratio_m15', 'bb_upper_m15', 'bb_lower_m15', 'rsi_m15'
            ]
            if not all(col in df_pd.columns for col in required_cols):
                missing = [col for col in required_cols if col not in df_pd.columns]
                logger.error(f"[{pair}] [DEBUG-SIG] Missing required indicator columns: {missing}")
                return None

            curr_close_h1 = df_pd['close_h1'].iloc[-1]
            curr_ema_fast_h1 = df_pd['ema_fast_h1'].iloc[-1]
            curr_ema_slow_h1 = df_pd['ema_slow_h1'].iloc[-1]
            
            curr_adx_m15 = df_pd['adx_m15'].iloc[-1]
            curr_hist_m15 = df_pd['hist_m15'].iloc[-1]
            prev_hist_m15 = df_pd['hist_m15_prev'].iloc[-1] 
            curr_close = df_pd["close" if "close" in df_pd.columns else "Close"].iloc[-1]
            
            # BB Squeeze用
            curr_sqz_ratio = df_pd['bb_squeeze_ratio_m15'].iloc[-1]
            curr_bb_upper = df_pd['bb_upper_m15'].iloc[-1]
            curr_bb_lower = df_pd['bb_lower_m15'].iloc[-1]
            curr_rsi_m15 = df_pd['rsi_m15'].iloc[-1]
            
            # ---------------------------------------------------------
            # エントリーロジック: Trend
            # ---------------------------------------------------------
            h1_long_cond = (curr_close_h1 > curr_ema_fast_h1) and (curr_ema_fast_h1 > curr_ema_slow_h1)
            h1_short_cond = (curr_close_h1 < curr_ema_fast_h1) and (curr_ema_fast_h1 < curr_ema_slow_h1)
                
            m15_long_cond = (curr_hist_m15 > prev_hist_m15)
            m15_short_cond = (curr_hist_m15 < prev_hist_m15)
            
            trend_signal = None
            adx_threshold = wfa_params_trend.get("adx_threshold", 20)
            use_adx = self.cfg.get_sync(f"strategy_filters.{pair}.use_adx", True)

            # ＝＝＝ ▼ 修正：ADXフィルター厳格化（全曜日適用） ▼ ＝＝＝
            adx_passed = (curr_adx_m15 >= adx_threshold) if use_adx else True
            
            # ＝＝＝ ▼ 修正：enabled_strategies 辞書フラグの対応 ▼ ＝＝＝
            enabled_strategies = self.cfg.get_sync("trading.enabled_strategies", {"trend": True})
            enable_trend = enabled_strategies.get("trend", True)
            enable_bb_squeeze = enabled_strategies.get("bb_squeeze", False)

            if enable_trend and adx_passed:
                if h1_long_cond and m15_long_cond: trend_signal = "long"
                elif h1_short_cond and m15_short_cond: trend_signal = "short"

            sqz_signal = None

            if enable_bb_squeeze:
                sqz_pctl = wfa_params_sqz.get("bb_squeeze_pctl", 0.85)
                rsi_long_th = wfa_params_sqz.get("rsi_long_th", 55)
                rsi_short_th = wfa_params_sqz.get("rsi_short_th", 45)

                is_squeezing = (curr_sqz_ratio >= sqz_pctl)
                
                if is_squeezing:
                    if (curr_close > curr_bb_upper) and (curr_rsi_m15 >= rsi_long_th):
                        sqz_signal = "long"
                    elif (curr_close < curr_bb_lower) and (curr_rsi_m15 <= rsi_short_th):
                        sqz_signal = "short"

            logger.info(
                f"[{pair}] [DEBUG-SIG-EVAL] H1(Long={h1_long_cond}, Short={h1_short_cond}) | "
                f"M15(Long={m15_long_cond}, Short={m15_short_cond}) | "
                f"ADX={curr_adx_m15:.1f}/{adx_threshold} (Passed={adx_passed}) | "
                f"TrendSig={trend_signal} | SqzSig={sqz_signal}"
            )

            # ---------------------------------------------------------
            # シグナル競合の調停 (優先順位付け)
            # ---------------------------------------------------------
            final_signal = None
            strategy_name = "trend"
            adx_threshold = wfa_params_trend.get("adx_threshold", 20)

            if trend_signal and sqz_signal:
                if curr_adx_m15 < adx_threshold:
                    final_signal = sqz_signal
                    strategy_name = "bb_squeeze"
                else:
                    final_signal = trend_signal
                    strategy_name = "trend"
            elif sqz_signal:
                final_signal = sqz_signal
                strategy_name = "bb_squeeze"
            elif trend_signal:
                final_signal = trend_signal
                strategy_name = "trend"

            if not final_signal:
                # ▼▼▼【修正】Signal Funnel ログの強化（除外理由の具体化） ▼▼▼
                reject_reason = "Technical conditions not met"
                if enable_trend and not adx_passed:
                    reject_reason = f"Trend rejected: ADX ({curr_adx_m15:.1f}) < Threshold ({adx_threshold})"
                elif enable_trend and adx_passed and not (h1_long_cond or h1_short_cond):
                    reject_reason = f"Trend rejected: H1 EMA cross condition failed (Fast={curr_ema_fast_h1:.3f}, Slow={curr_ema_slow_h1:.3f})"
                elif enable_bb_squeeze and not is_squeezing:
                    reject_reason = f"Squeeze rejected: Not squeezed. Ratio ({curr_sqz_ratio:.2f}) < Pctl ({sqz_pctl})"
                elif enable_bb_squeeze and is_squeezing:
                    if curr_close <= curr_bb_upper and curr_close >= curr_bb_lower:
                         reject_reason = f"Squeeze rejected: Price ({curr_close:.3f}) inside bands (Lower={curr_bb_lower:.3f}, Upper={curr_bb_upper:.3f})"
                    elif curr_rsi_m15 < rsi_long_th and curr_rsi_m15 > rsi_short_th:
                         reject_reason = f"Squeeze rejected: RSI ({curr_rsi_m15:.1f}) not in trigger zone (Long>={rsi_long_th}, Short<={rsi_short_th})"
                
                logger.info(f"[{pair}] [DEBUG-SIG] No signal match. Reason: {reject_reason}")
                # ▲▲▲ 修正ここまで ▲▲▲
                return None

            # ---------------------------------------------------------
            # ▼ 戦略別スコア計算分岐 & 動的ボリュームコントローラー ▼
            # ---------------------------------------------------------
            score = 0.40 # ベーススコア
            
            if strategy_name == "trend":
                # ADXによるトレンドの強さ
                if curr_adx_m15 > adx_threshold: 
                    score += 0.15
                if curr_adx_m15 > adx_threshold + 5:
                    score += 0.10
                    
                # H1上位足とのトレンド一致
                if final_signal == "long" and h1_long_cond:
                    score += 0.15
                elif final_signal == "short" and h1_short_cond:
                    score += 0.15
                    
                # RSIのモメンタム
                if final_signal == "long" and curr_rsi_m15 > 60:
                    score += 0.10
                elif final_signal == "short" and curr_rsi_m15 < 40:
                    score += 0.10
            
            elif strategy_name == "bb_squeeze":
                sqz_pctl = wfa_params_sqz.get("bb_squeeze_pctl", 0.85)
                
                # スクイーズ強度
                if curr_sqz_ratio >= sqz_pctl + 0.05:
                    score += 0.15
                if curr_sqz_ratio >= sqz_pctl + 0.10:
                    score += 0.10
                    
                # MACDモメンタム
                macd_up = curr_hist_m15 > prev_hist_m15
                macd_down = curr_hist_m15 < prev_hist_m15
                if final_signal == "long" and macd_up:
                    score += 0.15
                elif final_signal == "short" and macd_down:
                    score += 0.15
                    
                # H1上位足サポート
                if final_signal == "long" and h1_long_cond:
                    score += 0.10
                elif final_signal == "short" and h1_short_cond:
                    score += 0.10

            import math
            final_score = min(score, 1.0)
            
            # WFAの動的パラメータを取得
            active_wfa = wfa_params_sqz if strategy_name == "bb_squeeze" else wfa_params_trend
            th_full = active_wfa.get("ai_score_full_lot", 0.70)
            th_half = active_wfa.get("ai_score_half_lot", 0.55)
            # 過熱ゾーンの閾値（WFAになければデフォルト0.85を適用）
            th_overheat = active_wfa.get("ai_score_overheat", 0.85)
            
            # 💡【重要修正】position_units や tp_sl に渡すためのSL下限バリア値をここで抽出
            wfa_min_sl = active_wfa.get("min_sl_pips", None)

            # 🛡️【修正：AIスコア上限超過バグの撤廃】
            if math.isnan(final_score):
                logger.info(f"[{pair}] [DEBUG-SIG] Rejected: Score is NaN")
                return None  
            
            # ボリュームコントローラーの適用（過熱抑制版）
            if final_score >= th_overheat:
                lot_multiplier = 0.5
            elif final_score >= th_full:
                lot_multiplier = 1.0
            elif final_score >= th_half:
                lot_multiplier = 0.5
            else:
                logger.info(f"[{pair}] [DEBUG-SIG] Rejected: Score {final_score:.2f} < threshold {th_half}")
                return None

            # ＝＝＝ ▼ 環境適応型ボラティリティフィルター（ライブ用） ▼ ＝＝＝
            max_atr_ratio = active_wfa.get("max_atr_ratio_threshold", 999.0)
            vol_penalty = active_wfa.get("volatility_lot_penalty", 1.0)

            if "atr" in df_pd.columns:
                atr_val_current = df_pd["atr"].iloc[-1]
                atr_mean_24 = df_pd["atr"].tail(24).mean()
                if atr_mean_24 > 0:
                    atr_ratio = atr_val_current / atr_mean_24
                    if atr_ratio > max_atr_ratio:
                        lot_multiplier *= vol_penalty

            atr_val = df_pd["atr"].tail(5).mean()
            pip_mult = 100.0 if "JPY" in pair else 10000.0
            atr_pips = atr_val * pip_mult
            
            current_spread_pips = 0.0
            if "spread" in df_pd.columns:
                current_spread_pips = df_pd["spread"].iloc[-1] * pip_mult

            summary = await self.executor.get_account_summary()
            balance = summary.get("balance", 1_000_000)

            # 💡 position_units に wfa_min_sl_pips を渡す
            units = await position_units(
                executor=self.executor, balance=balance, atr_pips=atr_pips, 
                pair=pair, lot_ratio=lot_multiplier, ai_score=final_score,
                entry_price=curr_close, wfa_min_sl_pips=wfa_min_sl
            )
            if units == 0: return None

            # 💡 tp_sl に wfa_min_sl_pips を渡す
            tp, sl, trail_distance, use_gslo = await tp_sl(
                pair=pair, side=final_signal, price=curr_close, atr_pips=atr_pips,
                strategy_name=strategy_name, adx_val=curr_adx_m15,
                current_spread_pips=current_spread_pips, wfa_min_sl_pips=wfa_min_sl
            )
            
            tsl_trail_pct = active_wfa.get("tsl_trail_pct", 0.20)
            tsl_activation_pct = active_wfa.get("tsl_activation_pct", 0.40)
            ml_score = kwargs.get("ml_score", 0.0)

            return {
                "signal": final_signal, 
                "pair": pair, 
                "units": units, 
                "tp": tp,       
                "sl": sl,       
                "trail": trail_distance, 
                "tsl_activation_pct": tsl_activation_pct,
                "tsl_trail_pct": tsl_trail_pct,
                "atr_pips": atr_pips, 
                "spread_pips": round(current_spread_pips, 2),
                "use_gslo": use_gslo, 
                "entry_price": curr_close,
                "score": final_score, 
                "ml_score": float(ml_score),
                "strategy": strategy_name,
                "context": {
                    "tsl_activation_pct": tsl_activation_pct,
                    "strategy": strategy_name,
                    "ml_score": float(ml_score),
                    "spread_pips": round(current_spread_pips, 2)
                }
            }

        except Exception as e:
            import traceback
            logger.error(f"function_error in generate_signal for {pair}: {e}\n{traceback.format_exc()}")
            return None

    async def _check_and_execute_tsl(self, trade: Dict, mid_price: float):
        """
        トレーリングストップの条件をチェックし、必要であれば実行する。
        【改修版】建値バリア10%搭載・段階的TSL (Stepped TSL)
        """
        trade_id = trade.get("id")
        initial_trail_dist = trade.get("trail_dist")

        if initial_trail_dist is None or initial_trail_dist <= 0: return

        side = trade.get("side") or trade.get("direction")
        if not side: return

        required_keys = ["entry_price", "tp_price", "sl_price"]
        if not all(k in trade and trade[k] is not None for k in required_keys): return

        entry_price = trade["entry_price"]
        tp_price = trade["tp_price"]
        current_sl = trade["sl_price"]

        if tp_price == 0.0: return

        profit_target = abs(tp_price - entry_price)
        current_profit = (mid_price - entry_price) if side == "long" else (entry_price - mid_price)

        context = trade.get("context", {})
        activation_pct = context.get("tsl_activation_pct", 0.40) 
        activation_threshold = profit_target * activation_pct

        if current_profit < activation_threshold: return

        # ＝＝＝ ▼ 修正：段階的TSL（利益成長に応じて追従距離をタイト化） ▼ ＝＝＝
        profit_pct = current_profit / profit_target
        if profit_pct >= 0.80:
            current_trail_dist = profit_target * 0.10  # 80%到達：TP幅の10%で厳重に追従
        elif profit_pct >= 0.60:
            current_trail_dist = profit_target * 0.15  # 60%到達：TP幅の15%で追従
        else:
            current_trail_dist = initial_trail_dist    # デフォルト
        # ＝＝＝ ▲ 修正 ここまで ▲ ＝＝＝

        # ＝＝＝ 2. 新しいSL価格の計算 ＝＝＝
        new_sl_price = (mid_price - current_trail_dist) if side == "long" else (mid_price + current_trail_dist)

        # ＝＝＝ ▼ 修正：建値バリアをTPの5%から10%に引き上げ ▼ ＝＝＝
        floor_profit = profit_target * 0.10
        # ＝＝＝ ▲ 修正 ここまで ▲ ＝＝＝
        
        if side == "long":
            min_allowed_sl = entry_price + floor_profit
            should_update = (new_sl_price > current_sl) and (new_sl_price >= min_allowed_sl)
        else:
            max_allowed_sl = entry_price - floor_profit
            should_update = (new_sl_price < current_sl) and (new_sl_price <= max_allowed_sl)

        # ＝＝＝ 4. SLの更新実行 ＝＝＝
        if should_update:
            logger.warning(f"[TSL] Condition MET for trade {trade_id}! Updating SL from {current_sl:.5f} to {new_sl_price:.5f}")
            await self.executor.update_trade_stop_loss(trade_id=trade_id, new_sl_price=new_sl_price)