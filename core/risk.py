# risk.py (v16.0 - Dynamic Allocation & Anti-Martingale Integration)
from __future__ import annotations
from typing import Tuple, Optional, TYPE_CHECKING
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import math
from core import cfg
from core.logger import get_logger, append_csv, log_error
from core.hwm_tracker import HWMTracker, hwm_tracker

logger = get_logger(cfg, "risk")

if TYPE_CHECKING:
    from core.trade import TradeExecutor

DATA_DIR = cfg.project_root / cfg.get_sync("system.data_dir", "./data")

# ＝＝＝ ▼ Z2アーキテクチャ: 為替換算プロバイダの定義 ▼ ＝＝＝
class ConversionProvider:
    def get_pip_value_jpy(self, pair: str) -> float:
        """1ロット(10万通貨)あたり、1pipが何円の価値を持つかを返す"""
        pass

class StaticBacktestProvider(ConversionProvider):
    def __init__(self):
        # バックテスト開始時の各通貨の対円レート (Z2: 静的モック構造の保存)
        self.rates = {
            "USD": 160.0,
            "GBP": 210.0,
            "EUR": 180.0,
            "AUD": 110.0,
            "CAD": 110.0,
            "CHF": 160.0
        }

    def get_pip_value_jpy(self, pair: str) -> float:
        if "JPY" in pair:
            return 1000.0
        clean_pair = pair.replace("_", "")
        quote_currency = clean_pair[3:] if len(clean_pair) == 6 else "USD"
        pip_value_quote = 10.0
        jpy_rate = self.rates.get(quote_currency, 160.0)
        return pip_value_quote * jpy_rate

class LiveConversionProvider(ConversionProvider):
    """ライブトレード用の動的為替換算プロバイダ"""
    def __init__(self):
        # 起動時のフォールバック用初期レート
        self.rates = {
            "USD": 160.0,
            "GBP": 210.0,
            "EUR": 180.0,
            "AUD": 110.0,
        }

    def update_rate(self, currency: str, rate: float):
        """ストリーム等から取得した最新の対円レートを更新する"""
        if rate > 0:
            self.rates[currency] = rate

    def get_pip_value_jpy(self, pair: str) -> float:
        if "JPY" in pair:
            return 1000.0
        
        # 決済通貨（Quote Currency）を抽出
        clean_pair = pair.replace("_", "")
        quote_currency = clean_pair[3:] if len(clean_pair) == 6 else "USD"
        
        # 1ロット(10万通貨)あたりの決済通貨建てpip価値は固定で 10 unit
        pip_value_quote = 10.0
        
        # キャッシュされた最新の対円レートを掛け算
        jpy_rate = self.rates.get(quote_currency, 160.0)
        return pip_value_quote * jpy_rate

# 本番・検証環境用の為替換算真実（Single Source of Truth）
conversion_provider = LiveConversionProvider()
# ＝＝＝ ▲ Z2プロバイダ定義 ここまで ▲ ＝＝＝

def calculate_lot_multiplier_from_score(score: float | None) -> float:
    """
    与えられたスコアに基づいて、configで定義された階層に従いロット倍率を返す。
    スコアが閾値未満の場合は0.0を返す。
    """
    ai_cfg = cfg.get_sync("ai", {})
    if not ai_cfg or score is None:
        return 1.0  # AI設定がないかスコアがなければデフォルト倍率1.0

    tiers = ai_cfg.get("lot_ratio_tiers", {})
    th_full = tiers.get("full", 0.70)
    th_half = tiers.get("half", 0.55)

    if score >= th_full:
        return 1.0  # フルロット
    elif score >= th_half:
        return 0.5  # ハーフロット
    else:
        logger.info(f"Score {score:.2f} is below the minimum threshold {th_half}. Lot multiplier set to 0.0.")
        return 0.0  # 取引見送り

def calculate_virtual_lot(score: float, balance: float, atr_pips: float, pair: str) -> int:
    """タスク1：AIスコアに基づく仮想ロット計算 (旧互換用)"""
    if score < 0.55:
        return 0
    base_risk = 0.01
    multiplier = 1.0 if score >= 0.70 else 0.5
    risk_amount = balance * base_risk * multiplier
    pip_value = cfg.get_sync(f"instruments.{pair}.pip_value", default=0.01)
    if atr_pips <= 0:
        return 0
    units = int(risk_amount / (atr_pips * pip_value))
    return max(0, units)

def dynamic_risk_pct(atr_pips: float, balance: float) -> float:
    """(旧互換用)"""
    base_risk = cfg.get_sync("risk_management.base_risk_pct", default=0.01)
    atr_th_low, atr_th_high = cfg.get_sync("risk_management.atr_thresholds", default=[8.0, 20.0])
    balance_th = cfg.get_sync("risk_management.balance_threshold", default=0.5) * cfg.get_sync("trading.balance_init", 1_000_000)
    
    if atr_pips <= atr_th_low: atr_factor = 1.2
    elif atr_pips >= atr_th_high: atr_factor = 0.5
    else: atr_factor = 1.2 - (atr_pips - atr_th_low) * 0.7 / (atr_th_high - atr_th_low)
    
    balance_factor = 0.7 if balance < balance_th else 1.0
    drawdown_pct = 0.0
    log_file = cfg.project_root / cfg.get_sync("system.log_dir", "./logs") / f"risk_log_{datetime.now(timezone.utc):%Y%m%d}.csv"
    try:
        if log_file.exists():
            df = pd.read_csv(log_file)
            peak_balance = df["balance"].max()
            drawdown_pct = (peak_balance - balance) / peak_balance if peak_balance > 0 else 0.0
    except Exception as e:
        logger.error("dynamic_risk_pct_read_log", error=str(e))
        
    drawdown_factor = 0.5 if drawdown_pct > 0.1 else 1.0
    return base_risk * atr_factor * balance_factor * drawdown_factor

async def log_risk_calculation(pair: str, balance: float, atr_pips: float, risk_pct: float, units: int, exposure_ratio: float, drawdown_pct: float, corr_adjust: float, ai_score: float | None, ai_lot_mult: float) -> None:
    await append_csv(
        cfg,
        f"risk_log_{datetime.now(timezone.utc):%Y%m%d}.csv",
        pd.DataFrame([{
            "timestamp": datetime.now(timezone.utc).isoformat(), "pair": pair, "balance": balance,
            "atr_pips": atr_pips, "risk_pct": risk_pct, "units": units,
            "exposure_ratio": exposure_ratio, "drawdown_pct": drawdown_pct, "corr_adjust": corr_adjust,
            "ai_score": ai_score, "ai_lot_multiplier": ai_lot_mult
        }])
    )

def dynamic_sl_mult(atr_pips: float) -> float:
    """(旧互換用)"""
    lo, hi = 8.0, 20.0
    if atr_pips <= lo: return 1.5
    if atr_pips >= hi: return 2.5
    return 1.5 + (atr_pips - lo) * 1.0 / (hi - lo)

def dynamic_tp_mult(adx_val: float) -> float:
    """(旧互換用)"""
    if adx_val <= 20: return 1.2
    if adx_val >= 25: return 1.8
    return 1.2 + (adx_val - 20) * 0.6 / 5.0

def dynamic_trail_mult(di_plus: float, di_minus: float) -> float:
    """(旧互換用)"""
    di_diff = abs(di_plus - di_minus)
    if di_diff >= 20.0: return 1.5
    if di_diff <= 10.0: return 1.0
    return 1.2 + (di_diff - 10.0) * 0.3 / 10.0

def _calculate_trade_parameters(pair: str, atr_pips: float, wfa_min_sl_pips: float | None = None):
    """
    ダッシュボードで設定した基本SLとATRを組み合わせ、動的なSL/TP値幅を計算する。
    【追加】SLの下限（Floor）バリア機能を搭載し、ノイズでの狩られを防止。
    """
    risk_params = cfg.get_sync("risk_management", {})
    
    base_sl_pips = risk_params.get("base_sl_pips", 10.0)
    target_rr_ratio = risk_params.get("target_rr_ratio", 1.2)
    use_dynamic_atr = risk_params.get("use_dynamic_atr", True)
    
    multiplier = 1.0
    if use_dynamic_atr:
        safe_atr = max(0.1, atr_pips)
        raw_multiplier = safe_atr / 15.0  # 15分足の基準ATRを15.0とする
        multiplier = max(0.5, min(raw_multiplier, 2.0))
        
    dynamic_sl_pips = base_sl_pips * multiplier
    
    # ＝＝＝ ▼ 修正：動的ハイブリッド方式によるSL下限バリアの優先順位適用 ▼ ＝＝＝
    floor_sl_pips = None
    if wfa_min_sl_pips is not None and wfa_min_sl_pips > 0:
        floor_sl_pips = float(wfa_min_sl_pips)  # 第1優先: WFA最適化値
    elif pair in risk_params.get("min_sl_pips", {}):
        floor_sl_pips = float(risk_params["min_sl_pips"][pair])  # 第2優先: configの通貨ペア別設定
    else:
        floor_sl_pips = float(base_sl_pips)  # 第3優先: configの基本値
        
    dynamic_sl_pips = max(dynamic_sl_pips, floor_sl_pips)
    # ＝＝＝ ▲ 修正 ここまで ▲ ＝＝＝
    
    # TPは、底上げされたSLに対して常に目標リスクリワード（例: 1.2倍）を維持する
    dynamic_tp_pips = dynamic_sl_pips * target_rr_ratio
    
    return dynamic_sl_pips, dynamic_tp_pips, multiplier

async def position_units(
    executor: "TradeExecutor", 
    balance: float, 
    atr_pips: float, 
    pair: str, 
    lot_ratio: float = 1.0, 
    ai_score: float | None = None, 
    entry_price: float = 150.0,
    tracker: Optional[HWMTracker] = None,
    wfa_min_sl_pips: float | None = None  # 👈 追加
) -> int:
    """
    Excelの資金管理シートに基づくロット計算（動的資金配分 & アンチ・マーチンゲール対応版）
    """
    try:
        # 1. 基本シグナル倍率チェック
        if lot_ratio <= 0.0:
            return 0

        risk_params = cfg.get_sync("risk_management", {})
        current_tracker = tracker or hwm_tracker

        # 有効証拠金（Equity）の計算 (balance + 未決済損益)
        unrealized_pl = 0.0
        if hasattr(executor, "account_summary") and isinstance(executor.account_summary, dict):
            unrealized_pl = float(executor.account_summary.get("unrealized_pl", 0.0))
        current_equity = max(0.0, balance + unrealized_pl)

        # ＝＝＝ 1. HWMTrackerによるDDブレーキ & キルスイッチ判定 ＝＝＝
        dd_brake_mult = current_tracker.get_drawdown_multiplier(current_equity)
        if dd_brake_mult <= 0.0:
            logger.critical(
                f"🚨 [KILL-SWITCH ACTIVE] Drawdown limit reached (Equity: {current_equity:,.0f} JPY). Trade blocked for {pair}."
            )
            return 0

        # ＝＝＝ 2. 通貨ペア別PF傾斜配分（アクセル） ＝＝＝
        dyn_cfg = cfg.get_sync("dynamic_allocation", {})
        pair_multiplier = 1.0
        if dyn_cfg.get("enabled", True):
            min_mult = dyn_cfg.get("multiplier_min", 0.5)
            max_mult = dyn_cfg.get("multiplier_max", 1.5)
            pair_multipliers = dyn_cfg.get("pair_multipliers", {})
            raw_mult = pair_multipliers.get(pair, 1.0)
            pair_multiplier = max(min_mult, min(raw_mult, max_mult))

        # 3. 割り当て証拠金とリスク許容割合の取得
        allocated_margin = risk_params.get("allocated_margin_per_pair", 250000.0)
        risk_tolerance_pct = risk_params.get("risk_tolerance_pct", 0.015)
        invest_ratio = risk_params.get("invest_ratio", 0.5)

        # ＝＝＝ 3. ロット計算の修正 (許容損失額にPF傾斜倍率とDDブレーキ倍率を乗算) ＝＝＝
        theoretical_risk_jpy = (
            allocated_margin * risk_tolerance_pct * lot_ratio * pair_multiplier * dd_brake_mult
        )

        # ＝＝＝ 4. ポートフォリオ全体の最大エクスポージャー制限 ＝＝＝
        anti_mart_cfg = cfg.get_sync("anti_martingale", {})
        if anti_mart_cfg.get("enabled", True):
            total_max_exposure_pct = anti_mart_cfg.get("total_max_exposure_pct", 0.05)
            max_allowed_risk_jpy = current_equity * total_max_exposure_pct
            
            existing_total_risk_jpy = 0.0
            try:
                open_trades_dict = {}
                if hasattr(executor, "get_all_open_trades") and callable(executor.get_all_open_trades):
                    open_trades_dict = executor.get_all_open_trades() or {}

                if open_trades_dict:
                    for p_name, t_list in open_trades_dict.items():
                        p_pip_val = conversion_provider.get_pip_value_jpy(p_name)
                        for t in (t_list or []):
                            sl_dist = abs(float(t.get("entry_price", 0.0)) - float(t.get("sl_price", 0.0)))
                            pip_sz = 0.01 if "JPY" in p_name else 0.0001
                            p_pips = sl_dist / pip_sz if pip_sz > 0 else 0
                            units = float(t.get("current_units", 0))
                            existing_total_risk_jpy += (units / 100000.0) * p_pips * p_pip_val

                elif hasattr(executor, "positions"):
                    for pos in getattr(executor, "positions", []):
                        p_name = pos.get("pair", "")
                        p_pip_val = conversion_provider.get_pip_value_jpy(p_name)
                        sl_dist = abs(float(pos.get("entry_price", 0.0)) - float(pos.get("sl_price", 0.0)))
                        pip_sz = 0.01 if "JPY" in p_name else 0.0001
                        p_pips = sl_dist / pip_sz if pip_sz > 0 else 0
                        units = float(pos.get("lot_size", pos.get("units", 0)))
                        existing_total_risk_jpy += (units / 100000.0) * p_pips * p_pip_val
            except Exception as e:
                logger.warning(f"Could not calculate existing total risk: {e}")

            if (existing_total_risk_jpy + theoretical_risk_jpy) > max_allowed_risk_jpy:
                logger.warning(
                    f"🛡️ [EXPOSURE LIMIT] Total portfolio risk ({existing_total_risk_jpy + theoretical_risk_jpy:,.0f} JPY) "
                    f"exceeds max limit ({max_allowed_risk_jpy:,.0f} JPY, {total_max_exposure_pct*100:.1f}% of equity). Trade blocked for {pair}."
                )
                return 0

        # 5. ATR異常値対策
        safe_atr_pips = atr_pips
        if safe_atr_pips > 100.0 or safe_atr_pips <= 0.0:
            safe_atr_pips = 15.0

        # 6. 動的SL値幅の取得（ATR伸縮）👈 wfa_min_sl_pips を渡すように修正
        dynamic_sl_pips, _, multiplier = _calculate_trade_parameters(pair, safe_atr_pips, wfa_min_sl_pips)

        # 7. Lot数の逆算 (Z2多通貨対応版)
        pip_value_per_lot = conversion_provider.get_pip_value_jpy(pair)
        raw_lot = theoretical_risk_jpy / (dynamic_sl_pips * pip_value_per_lot)
        actual_lot = math.floor(raw_lot * 100) / 100.0
        units_by_risk = int(actual_lot * 100000)

        # 8. 証拠金に対する投資割合(レバレッジ)によるハードリミット
        default_margin = 0.05 if "GBP" in pair else 0.04
        margin_rate = risk_params.get("margin_rates", {}).get(pair, default_margin)
        max_leverage = int(1.0 / margin_rate)
        effective_margin = allocated_margin * pair_multiplier * dd_brake_mult * lot_ratio
        max_notional_value = effective_margin * invest_ratio * max_leverage
        units_by_leverage = int(max_notional_value / entry_price)

        # 9. 安全な方を最終ロットとして採用
        final_units = min(units_by_risk, units_by_leverage)

        if final_units <= 0:
            return 0

        # 10. ログ記録 (新設倍率も併せて追跡)
        exposure_ratio = final_units * entry_price / allocated_margin if allocated_margin > 0 else 0
        current_dd_pct = current_tracker.get_current_drawdown_pct(current_equity)
        combined_lot_mult = pair_multiplier * dd_brake_mult
        await log_risk_calculation(
            pair, allocated_margin, safe_atr_pips, risk_tolerance_pct, 
            final_units, exposure_ratio, current_dd_pct, multiplier, ai_score, combined_lot_mult
        )
        
        return final_units

    except Exception as e:
        await log_error(logger, "position_units", error=e)
        return 0

async def tp_sl(
    pair: str, 
    side: str, 
    price: float, 
    atr_pips: float, 
    strategy_name: str, 
    adx_val: float | None = None, 
    current_spread_pips: float = 0.0,
    wfa_min_sl_pips: float | None = None  # 👈 追加
) -> Tuple[float, float, float, bool]:
    """
    TP/SL価格の堅牢な算出（多通貨・Z2対応版）
    【改修】リアルタイムスプレッドをSL側にのみ加算し、ノイズ狩りを防止するクッション機能を実装。
    """
    try:
        # ATR異常値対策
        safe_atr_pips = atr_pips
        if safe_atr_pips > 100.0 or safe_atr_pips <= 0.0:
            safe_atr_pips = 15.0

        # 通貨ペアに基づいた動的パラメータ取得 👈 wfa_min_sl_pips を渡すように修正
        dynamic_sl_pips, dynamic_tp_pips, multiplier = _calculate_trade_parameters(pair, safe_atr_pips, wfa_min_sl_pips)

        # ＝＝＝ ▼ 追加：スプレッド負け防止クッション ▼ ＝＝＝
        # SL距離に現在のリアルタイムスプレッド（pips）を加算する。
        dynamic_sl_pips += current_spread_pips
        # ＝＝＝ ▲ 追加 ここまで ▲ ＝＝＝

        # 通貨ペアに応じた pip_size の動的取得（クロス円なら0.01、その他なら0.0001）
        pip_size = 0.01 if "JPY" in pair else 0.0001
        
        sl_distance_price = dynamic_sl_pips * pip_size
        tp_distance_price = dynamic_tp_pips * pip_size

        if side == "long":
            sl = price - sl_distance_price
            tp = price + tp_distance_price
        else:
            sl = price + sl_distance_price
            tp = price - tp_distance_price

        # TSL幅の最適化
        risk_params = cfg.get_sync("risk_management", {})
        use_tsl = risk_params.get("use_trailing_stop", True) 
        
        trail = (tp_distance_price * 0.20) if use_tsl else 0.0

        use_gslo = False

        return tp, sl, trail, use_gslo
    except Exception as e:
        logger.error(f"Error in tp_sl: {e}")
        return 0.0, 0.0, 0.0, False