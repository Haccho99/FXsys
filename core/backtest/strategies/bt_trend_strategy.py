# core/backtest/strategies/bt_trend_strategy.py
import polars as pl
from typing import Dict, Any
import redis.asyncio as aioredis
from datetime import datetime
import pytz

from core.logger import get_logger
from core import cfg

logger = get_logger(cfg, "bt_trend_strategy")

class BacktestTrendStrategy:
    def __init__(self, params: dict, redis: aioredis.Redis, cfg_manager):
        self.params = params
        self.redis = redis
        self.cfg = cfg_manager

    def _calculate_confidence_score(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        自信度スコアを計算。過学習を防ぐため、WFA動的パラメータのADX閾値を参照。
        """
        adx_th = self.params.get("adx_threshold", 20)
        
        score_expr = (
            pl.lit(0.5)  # 1. 基本スコア
            + pl.when(pl.col("adx_m15") > adx_th).then(pl.lit(0.15)).otherwise(pl.lit(0)) 
            + pl.when(pl.col("rsi") > 55).then(pl.lit(0.1)).otherwise(pl.lit(0)) 
        )
        return df.with_columns(score=score_expr)

    def _apply_time_filters(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Polarsによる高速な時間帯フィルタリング。
        config.json から system.time_filters, system.active_windows を読み込み、禁止フラグを立てる。
        """
        try:
            system_cfg = self.cfg.get_sync("system", {})
            time_filters = system_cfg.get("time_filters", {})
            active_windows_cfg = system_cfg.get("active_windows", {})
            pair = self.params.get("pair", "")

            # ベースとなる時間情報を付与
            df_time = df.with_columns([
                pl.col("time").dt.convert_time_zone("Asia/Tokyo").alias("jst_time")
            ]).with_columns([
                pl.col("jst_time").dt.hour().alias("hour"),
                pl.col("jst_time").dt.minute().alias("minute"),
                pl.col("jst_time").dt.weekday().alias("weekday") # Polars: 1=Mon ... 7=Sun
            ])

            is_forbidden = pl.lit(False)

            # 1. グローバル時間帯フィルター & 曜日フィルター
            if time_filters:
                friday_stop_hour = time_filters.get("friday_stop_hour", 20)
                
                # 週末停止 (金曜指定時間以降、土日)
                weekend_cond = (
                    ((pl.col("weekday") == 5) & (pl.col("hour") >= friday_stop_hour)) |
                    (pl.col("weekday").is_in([6, 7]))
                )
                is_forbidden = is_forbidden | weekend_cond

                # 木曜制限 (Polars weekday=4)
                thursday_filters = time_filters.get("thursday_filters", {})
                if thursday_filters.get("enabled", False):
                    forbidden_hours = thursday_filters.get("forbidden_hours", [])
                    forbidden_pairs = thursday_filters.get("forbidden_pairs", [])
                    if pair in forbidden_pairs:
                        thu_cond = (pl.col("weekday") == 4) & (pl.col("hour").is_in(forbidden_hours))
                        is_forbidden = is_forbidden | thu_cond

                # 全体禁止時間帯
                f_start_str = time_filters.get("forbidden_start")
                f_end_str = time_filters.get("forbidden_end")
                if f_start_str and f_end_str:
                    start_h, start_m = map(int, f_start_str.split(':'))
                    end_h, end_m = map(int, f_end_str.split(':'))
                    time_mins = pl.col("hour").cast(pl.Int32) * 60 + pl.col("minute").cast(pl.Int32)
                    start_mins = start_h * 60 + start_m
                    end_mins = end_h * 60 + end_m
                    if start_mins <= end_mins:
                        time_cond = (time_mins >= start_mins) & (time_mins <= end_mins)
                    else:
                        time_cond = (time_mins >= start_mins) | (time_mins <= end_mins)
                    is_forbidden = is_forbidden | time_cond

            # 2. Active Windows判定
            pair_active_windows = active_windows_cfg.get(pair, [])
            if pair_active_windows:
                # いずれかのウィンドウに入っていればTrueとなるフラグを計算
                is_in_window = pl.lit(False)
                time_mins = pl.col("hour").cast(pl.Int32) * 60 + pl.col("minute").cast(pl.Int32)
                for window in pair_active_windows:
                    start_str = window.get("start")
                    end_str = window.get("end")
                    if start_str and end_str:
                        start_h, start_m = map(int, start_str.split(':'))
                        end_h, end_m = map(int, end_str.split(':'))
                        start_mins = start_h * 60 + start_m
                        end_mins = end_h * 60 + end_m
                        if start_mins <= end_mins:
                            win_cond = (time_mins >= start_mins) & (time_mins <= end_mins)
                        else:
                            win_cond = (time_mins >= start_mins) | (time_mins <= end_mins)
                        is_in_window = is_in_window | win_cond
                
                # ウィンドウに入っていない場合は禁止
                is_forbidden = is_forbidden | ~is_in_window

            return df_time.with_columns(is_forbidden_time=is_forbidden).drop(["jst_time", "hour", "minute", "weekday"])

        except Exception as e:
            logger.error(f"Error applying time filters in Polars: {e}")
            return df.with_columns(is_forbidden_time=pl.lit(False))

    async def generate_signal(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        H1 EMAのパーフェクトオーダーとM15 MACDヒストグラムの増減による
        「原点回帰ロジック（波乗り＋シグナルエグジット）」を実装。
        """
        required_cols = ["ema_fast_h1", "ema_slow_h1", "hist_m15", "hist_m15_prev", "atr", "close", "time"]
        if not all(col in df.columns for col in required_cols):
            logger.error(f"Missing required columns for trend strategy. Available: {df.columns}")
            return df.with_columns(
                entries=pl.lit(False), short_entries=pl.lit(False),
                signal_exits=pl.lit(False), short_signal_exits=pl.lit(False),
                sl_price=pl.lit(None, dtype=pl.Float64), tp_price=pl.lit(None, dtype=pl.Float64),
                lot_ratio=pl.lit(0.0)
            )

        sl_mult = self.params.get("sl_atr_multiplier", 2.0)
        tp_mult = self.params.get("tp_atr_multiplier", 2.5)

        df_filtered = self._apply_time_filters(df)

        # 1. エントリーロジック
        h1_uptrend = pl.col("ema_fast_h1") > pl.col("ema_slow_h1")
        h1_downtrend = pl.col("ema_fast_h1") < pl.col("ema_slow_h1")
        
        m15_momentum_up = pl.col("hist_m15") > pl.col("hist_m15_prev")
        m15_momentum_down = pl.col("hist_m15") < pl.col("hist_m15_prev")

        # ADXハードゲートの追加
        pair = self.params.get("pair", "")
        use_adx = self.cfg.get_sync(f"strategy_filters.{pair}.use_adx", True)
        adx_th = self.params.get("adx_threshold", 20)
        adx_passed = (pl.col("adx_m15") >= adx_th) if use_adx else pl.lit(True)

        long_entries = h1_uptrend & m15_momentum_up & adx_passed & ~pl.col("is_forbidden_time")
        short_entries = h1_downtrend & m15_momentum_down & adx_passed & ~pl.col("is_forbidden_time")

        # 2. エグジットロジック
        long_exits = pl.col("hist_m15") < pl.col("hist_m15_prev")
        short_exits = pl.col("hist_m15") > pl.col("hist_m15_prev")

        # 3. TP/SLの計算
        # ＝＝＝ ▼ 修正：Optunaバリア値の取得と Polars による安全なクランプ処理 ▼ ＝＝＝
        min_sl_pips = float(self.params.get("min_sl_pips", 10.0))
        
        # pair名がない場合のフォールバック: 平均価格が30以上ならクロス円(0.01)と推定
        is_jpy = True
        if "pair" in self.params:
            is_jpy = "JPY" in self.params["pair"]
        else:
            mean_close = df["close"].mean()
            is_jpy = mean_close > 30.0
            
        pip_size = 0.01 if is_jpy else 0.0001
        min_sl_price_dist = min_sl_pips * pip_size

        # ATR距離と下限バリア距離のうち、大きい方を動的に採用する（水平最大値）
        sl_dist_expr = pl.max_horizontal(pl.col("atr") * sl_mult, pl.lit(min_sl_price_dist))

        sl_price_expr = (
            pl.when(long_entries).then(pl.col("close") - sl_dist_expr)
            .when(short_entries).then(pl.col("close") + sl_dist_expr)
            .otherwise(None)
        )
        # ＝＝＝ ▲ 修正 ここまで ▲ ＝＝＝

        tp_price_expr = (
            pl.when(long_entries).then(pl.col("close") + pl.col("atr") * tp_mult)
            .when(short_entries).then(pl.col("close") - pl.col("atr") * tp_mult)
            .otherwise(None)
        )

        df_with_signals = df_filtered.with_columns(
            entries=long_entries.fill_null(False),
            short_entries=short_entries.fill_null(False),
            signal_exits=long_exits.fill_null(False),
            short_signal_exits=short_exits.fill_null(False),
            sl_price=sl_price_expr,
            tp_price=tp_price_expr,
        )

        # 4. スコアリングの実行
        final_df = self._calculate_confidence_score(df_with_signals)

        # ---------------------------------------------------------
        # ▼ 5. WFA自律適応フィルター & 動的ボリュームコントローラー ▼
        # ---------------------------------------------------------
        
        # 曜日フィルター (trade_friday)
        trade_friday = self.params.get("trade_friday", True)
        if not trade_friday:
            try:
                final_df = final_df.with_columns([
                    pl.when(pl.col("time").dt.convert_time_zone("Asia/Tokyo").dt.weekday() == 5).then(False).otherwise(pl.col("entries")).alias("entries"),
                    pl.when(pl.col("time").dt.convert_time_zone("Asia/Tokyo").dt.weekday() == 5).then(False).otherwise(pl.col("short_entries")).alias("short_entries")
                ])
            except pl.exceptions.ComputeError:
                final_df = final_df.with_columns([
                    pl.when(pl.col("time").dt.weekday() == 5).then(False).otherwise(pl.col("entries")).alias("entries"),
                    pl.when(pl.col("time").dt.weekday() == 5).then(False).otherwise(pl.col("short_entries")).alias("short_entries")
                ])

        # 動的ボリュームコントローラー (lot_ratio) - 既存のmap_elementsロジックを完全置換
        full_lot_th = self.params.get("ai_score_full_lot", 0.70)
        half_lot_th = self.params.get("ai_score_half_lot", 0.55)
        max_score_th = self.params.get("max_ai_score", 0.95)
        overheat_th = self.params.get("ai_score_overheat", 0.85)

        if "score" in final_df.columns:
            final_df = final_df.with_columns(
                pl.when(pl.col("score").is_null() | pl.col("score").is_nan() | (pl.col("score") > max_score_th)).then(0.0)
                .when(pl.col("score") >= overheat_th).then(0.5)  # 過熱ゾーンはハーフロットに減衰
                .when(pl.col("score") >= full_lot_th).then(1.0)
                .when(pl.col("score") >= half_lot_th).then(0.5)
                .otherwise(0.0)
                .alias("lot_ratio")
            )

            # ＝＝＝ ▼ 追加：環境適応型ボラティリティフィルター（WFA対応） ▼ ＝＝＝
            max_atr_ratio = self.params.get("max_atr_ratio_threshold", 999.0)
            vol_penalty = self.params.get("volatility_lot_penalty", 1.0)

            if "atr" in final_df.columns:
                # 過去24本（約6時間）のATR平均を計算（min_periods=1でNaNクラッシュを回避）
                final_df = final_df.with_columns(
                    pl.col("atr").rolling_mean(window_size=24, min_periods=1).alias("atr_mean_24")
                )
                # 平均に対する現在のATRの乖離率を算出
                final_df = final_df.with_columns(
                    pl.when(pl.col("atr_mean_24") > 0).then(pl.col("atr") / pl.col("atr_mean_24")).otherwise(1.0).alias("atr_ratio")
                )
                # 閾値を超えた場合、既存のlot_ratioに対してペナルティを乗算（二重減衰を許容）
                final_df = final_df.with_columns(
                    pl.when(pl.col("atr_ratio") > max_atr_ratio)
                    .then(pl.col("lot_ratio") * vol_penalty)
                    .otherwise(pl.col("lot_ratio"))
                    .alias("lot_ratio")
                )
            # ＝＝＝ ▲ 追加 ここまで ▲ ＝＝＝
            
            # ロット倍率が0のシグナルは無効化（足切り）
            final_df = final_df.with_columns([
                pl.when(pl.col("lot_ratio") > 0).then(pl.col("entries")).otherwise(False).alias("entries"),
                pl.when(pl.col("lot_ratio") > 0).then(pl.col("short_entries")).otherwise(False).alias("short_entries")
            ])
        else:
            final_df = final_df.with_columns(pl.lit(1.0).alias("lot_ratio"))
        # ---------------------------------------------------------

        return final_df.drop("is_forbidden_time")