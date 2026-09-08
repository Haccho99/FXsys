# core/backtest/strategies/bt_bb_squeeze_strategy.py
import sys
from pathlib import Path
import polars as pl
from typing import Optional, Dict, Any

# プロジェクトのルートディレクトリをPythonパスに追加
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    # ▼▼▼【修正】'sys.sys.path' を 'sys.path' に修正 ▼▼▼
    sys.path.append(str(project_root))

from core.strategies.base.base_squeeze import BaseSqueezeStrategy
from core import cfg
from core.logger import get_logger

class BacktestBbSqueezeStrategy(BaseSqueezeStrategy):
    def __init__(self, params: Optional[Dict[str, Any]] = None, redis=None, cfg_manager=None):
        super().__init__(params)
        self.cfg = cfg_manager if cfg_manager is not None else cfg
        self.redis = redis
        self.logger = get_logger(self.cfg, "bt_bb_squeeze_strategy")
        self.logger.info(f"Initialized BacktestBbSqueezeStrategy with params: {self.params}")

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
            self.logger.error(f"Error applying time filters in Polars: {e}")
            return df.with_columns(is_forbidden_time=pl.lit(False))

    def _calculate_confidence_score(self, df: pl.DataFrame) -> pl.DataFrame:
        """bb_squeeze専用のスコアリングロジック（Polarsベクトル演算）"""
        sqz_pctl = self.params.get("bb_squeeze_pctl", 0.85)
        
        macd_up = pl.col("hist_m15") > pl.col("hist_m15_prev")
        macd_down = pl.col("hist_m15") < pl.col("hist_m15_prev")
        
        h1_uptrend = pl.col("ema_fast_h1") > pl.col("ema_slow_h1")
        h1_downtrend = pl.col("ema_fast_h1") < pl.col("ema_slow_h1")
        
        score_expr = (
            pl.lit(0.40)
            + pl.when(pl.col("bb_squeeze_ratio_m15") >= sqz_pctl + 0.05).then(pl.lit(0.15)).otherwise(pl.lit(0.0))
            + pl.when(pl.col("bb_squeeze_ratio_m15") >= sqz_pctl + 0.10).then(pl.lit(0.10)).otherwise(pl.lit(0.0))
            + pl.when((pl.col("entries") & macd_up) | (pl.col("short_entries") & macd_down)).then(pl.lit(0.15)).otherwise(pl.lit(0.0))
            + pl.when((pl.col("entries") & h1_uptrend) | (pl.col("short_entries") & h1_downtrend)).then(pl.lit(0.10)).otherwise(pl.lit(0.0))
        )
        return df.with_columns(score=score_expr)

    async def generate_signal(self, df_with_indicators: pl.DataFrame) -> pl.DataFrame:
        """
        指標計算済みのDataFrameからシグナルを計算し、元のDFにマージして返す。
        """
        try:
            required_cols = [
                "bb_width_m15", "bb_upper_m15", "bb_lower_m15", 
                "close", "hist_m15", "rsi_m15", "atr", "bb_squeeze_ratio_m15", "time"
            ]
            if not all(col in df_with_indicators.columns for col in required_cols):
                self.logger.error(f"Required columns for squeeze strategy missing. Skipping signal generation.")
                return df_with_indicators.with_columns(
                    entries=pl.lit(False), short_entries=pl.lit(False),
                    sl_price=pl.lit(None, dtype=pl.Float64), tp_price=pl.lit(None, dtype=pl.Float64),
                    signal_exits=pl.lit(False), short_exits=pl.lit(False)
                )

            # 時間帯フィルターの適用 (フラグ is_forbidden_time を追加)
            df_filtered = self._apply_time_filters(df_with_indicators)

            # スクイーズ判定
            sqz_pctl = self.params.get("bb_squeeze_pctl", 0.85)
            is_squeezed = pl.col("bb_squeeze_ratio_m15") >= sqz_pctl

            # RSIのWFA閾値取得
            rsi_long_th = self.params.get("rsi_long_th", 55)
            rsi_short_th = self.params.get("rsi_short_th", 45)

            # MACDとRSIの論理バグ修正
            use_macd = self.params.get("use_macd_filter", True)
            use_rsi = self.params.get("use_rsi_filter", True)

            macd_long_passed = (pl.col("hist_m15") > 0) if use_macd else pl.lit(True)
            macd_short_passed = (pl.col("hist_m15") < 0) if use_macd else pl.lit(True)

            rsi_long_passed = (pl.col("rsi_m15") >= rsi_long_th) if use_rsi else pl.lit(True)
            rsi_short_passed = (pl.col("rsi_m15") <= rsi_short_th) if use_rsi else pl.lit(True)

            # エントリー条件の構築
            long_entry_cond = is_squeezed & (pl.col("close") > pl.col("bb_upper_m15")) & \
                              macd_long_passed & rsi_long_passed & \
                              ~pl.col("is_forbidden_time")
            
            short_entry_cond = is_squeezed & (pl.col("close") < pl.col("bb_lower_m15")) & \
                               macd_short_passed & rsi_short_passed & \
                               ~pl.col("is_forbidden_time")

            entries = long_entry_cond.fill_null(False)
            short_entries = short_entry_cond.fill_null(False)

            # ＝＝＝ ▼ 修正：初期SL/TPの計算とOptunaバリア値のクランプ処理 ▼ ＝＝＝
            sl_mult = self.params.get("sl_atr_multiplier", 1.0)
            tp_mult = self.params.get("tp_atr_multiplier", 2.0)
            
            # Optunaバリア値の取得
            min_sl_pips = float(self.params.get("min_sl_pips", 10.0))
            
            # pair名がない場合のフォールバック: 平均価格が30以上ならクロス円(0.01)と推定
            is_jpy = True
            if "pair" in self.params:
                is_jpy = "JPY" in self.params["pair"]
            else:
                mean_close = df_with_indicators["close"].mean()
                is_jpy = mean_close > 30.0
                
            pip_size = 0.01 if is_jpy else 0.0001
            min_sl_price_dist = min_sl_pips * pip_size

            # ATR距離と下限バリア距離のうち、大きい方を動的に採用する（水平最大値）
            sl_dist_expr = pl.max_horizontal(pl.col("atr") * sl_mult, pl.lit(min_sl_price_dist))

            sl_price_expr = (
                pl.when(entries).then(pl.col("close") - sl_dist_expr)
                .when(short_entries).then(pl.col("close") + sl_dist_expr)
                .otherwise(None)
            )

            tp_price_expr = (
                pl.when(entries).then(pl.col("close") + pl.col("atr") * tp_mult)
                .when(short_entries).then(pl.col("close") - pl.col("atr") * tp_mult)
                .otherwise(None)
            )
            # ＝＝＝ ▲ 修正 ここまで ▲ ＝＝＝

            # 元のDFにシグナル列を追加して返す
            final_df = df_filtered.with_columns(
                entries=entries, short_entries=short_entries,
                sl_price=sl_price_expr, tp_price=tp_price_expr,
                signal_exits=pl.lit(False), short_exits=pl.lit(False)
            )

            # ---------------------------------------------------------
            # ▼ WFA自律適応フィルター & 動的ボリュームコントローラー ▼
            # ---------------------------------------------------------
            
            # 1. 曜日フィルター (trade_friday)
            trade_friday = self.params.get("trade_friday", True)
            if not trade_friday:
                try:
                    # 本番用：tz-aware想定でJSTへ変換し金曜日(5)を無効化
                    final_df = final_df.with_columns([
                        pl.when(pl.col("time").dt.convert_time_zone("Asia/Tokyo").dt.weekday() == 5).then(False).otherwise(pl.col("entries")).alias("entries"),
                        pl.when(pl.col("time").dt.convert_time_zone("Asia/Tokyo").dt.weekday() == 5).then(False).otherwise(pl.col("short_entries")).alias("short_entries")
                    ])
                except pl.exceptions.ComputeError:
                    # テスト環境用：tz-naive想定のフォールバック
                    final_df = final_df.with_columns([
                        pl.when(pl.col("time").dt.weekday() == 5).then(False).otherwise(pl.col("entries")).alias("entries"),
                        pl.when(pl.col("time").dt.weekday() == 5).then(False).otherwise(pl.col("short_entries")).alias("short_entries")
                    ])

            # ---------------------------------------------------------
            # ▼ スコアリングと動的ボリュームコントローラーの適用 ▼
            # ---------------------------------------------------------
            final_df = self._calculate_confidence_score(final_df)

            full_lot_th = self.params.get("ai_score_full_lot", 0.70)
            half_lot_th = self.params.get("ai_score_half_lot", 0.55)
            max_score_th = self.params.get("max_ai_score", 0.95)
            overheat_th = self.params.get("ai_score_overheat", 0.85)

            # スコアに基づくロット倍率の計算（過熱減衰対応）
            final_df = final_df.with_columns(
                pl.when(pl.col("score").is_null() | pl.col("score").is_nan() | (pl.col("score") > max_score_th)).then(0.0)
                .when(pl.col("score") >= overheat_th).then(0.5)  # 過熱ゾーン減衰
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
            
            # ロット倍率が0のシグナルは無効化（エントリーから除外）
            final_df = final_df.with_columns([
                pl.when(pl.col("lot_ratio") > 0.0).then(pl.col("entries")).otherwise(False).alias("entries"),
                pl.when(pl.col("lot_ratio") > 0.0).then(pl.col("short_entries")).otherwise(False).alias("short_entries")
            ])
            # ---------------------------------------------------------

            return final_df.drop("is_forbidden_time")

        except Exception as e:
            self.logger.error("Error during squeeze backtest signal generation", exc_info=True)
            return df_with_indicators