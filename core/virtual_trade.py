"""
virtual_trade.py - 仮想フォワードテスト用のブローカーエミュレーター (v5 - TP Trailing & Weekend Close)
"""
import uuid
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List
import pandas as pd
from pathlib import Path
from core.logger import get_logger, append_csv
from core import cfg

logger = get_logger(cfg, "virtual_trade")

# ==========================================
# core/virtual_trade.py の VirtualBroker クラス修正
# ==========================================
class VirtualBroker:
    def __init__(self, initial_balance: float = 1_000_000.0, is_backtest: bool = False):
        self.is_backtest = is_backtest
        self.balance = initial_balance
        self.positions: List[Dict] = []

        # 状態管理
        self.last_trade_time: Dict[str, datetime] = {}
        self.lockout_until: Dict[str, datetime] = {}
        self.price_history: Dict[str, List[tuple]] = {}
        self.latest_mid_prices: Dict[str, float] = {}
        
        # 連敗管理とクールダウン
        self.consecutive_losses: Dict[str, int] = {}
        self.last_close_time: Dict[str, datetime] = {}
        self.last_close_direction: Dict[str, str] = {}
        self.closed_positions_history: List[Dict] = []

        # ＝＝＝ ▼ 修正：__init__で設定を1度だけ確実に読み込む ▼ ＝＝＝
        self.time_filters = cfg.get_sync("system", {}).get("time_filters", {})
        if not self.time_filters:
            # cfg が空振った場合のみ直接読み込み
            try:
                project_root = Path(__file__).resolve().parent.parent
                config_path = project_root / 'config.json'
                if config_path.exists():
                    with open(config_path, 'r', encoding='utf-8') as f:
                        raw_cfg = json.load(f)
                        self.time_filters = raw_cfg.get("system", {}).get("time_filters", {})
            except Exception as e:
                logger.error(f"Failed to load time_filters in __init__: {e}")

        # 時刻文字列をパースして保持（ループ内の負荷をゼロにする）
        from datetime import datetime as dt_module
        self.f_start_t = None
        self.f_end_t = None
        self.friday_stop_hour = self.time_filters.get("friday_stop_hour", 20)
        
        f_start_str = self.time_filters.get("forbidden_start")
        f_end_str = self.time_filters.get("forbidden_end")
        if f_start_str and f_end_str:
            self.f_start_t = dt_module.strptime(f_start_str, "%H:%M").time()
            self.f_end_t = dt_module.strptime(f_end_str, "%H:%M").time()
        # ＝＝＝ ▲ 修正 ここまで ▲ ＝＝＝

        logger.info(f"VirtualBroker initialized with balance: {self.balance} (is_backtest={self.is_backtest})")
        if not self.is_backtest:
            self._save_open_positions_json()

    def _monitor_kill_switch(self, pair: str, current_price: float, current_time: datetime):
        """直近5分間の価格変動を監視し、パニック相場ならKill Switchを発動する"""
        history = self.price_history.setdefault(pair, [])
        history.append((current_time, current_price))

        # 5分（300秒）以上古いデータを破棄
        cutoff_time = current_time - timedelta(minutes=5)
        history = [h for h in history if h[0] > cutoff_time]
        self.price_history[pair] = history

        if len(history) > 1:
            max_price = max(h[1] for h in history)
            min_price = min(h[1] for h in history)
            price_diff = max_price - min_price

            risk_config = cfg.get_sync("risk_management", {})
            kill_switch_pips = risk_config.get("kill_switch_pips", 0.20)
            lockout_min = risk_config.get("kill_switch_lockout_min", 60)

            # 指定されたpips以上の変動でパニック相場と判定
            if price_diff >= kill_switch_pips:
                if pair not in self.lockout_until or current_time >= self.lockout_until[pair]:
                    logger.warning(f"🚨 [KILL SWITCH] {pair} dropped/spiked {price_diff:.3f} JPY in 5 mins! Locking out for {lockout_min} mins.")
                    self.lockout_until[pair] = current_time + timedelta(minutes=lockout_min)

                # 発動後は履歴をリセットし、連続発動を防ぐ
                self.price_history[pair] = []

    def can_open_position(self, pair: str, current_time, direction: str) -> bool:
        """エントリー前に5つの防護壁をチェックする（超軽量版）"""
        
        # ＝＝＝ ▼ 修正：軽量な型変換（Pandasを使わない） ▼ ＝＝＝
        import pytz
        try:
            if isinstance(current_time, str):
                current_time = datetime.fromisoformat(current_time.replace("Z", "+00:00"))
            
            if hasattr(current_time, 'tzinfo') and current_time.tzinfo is None:
                current_time = current_time.replace(tzinfo=timezone.utc)
            elif not hasattr(current_time, 'tzinfo'):
                # numpy.datetime64 等への究極のフォールバック
                current_time = pd.to_datetime(current_time, utc=True).to_pydatetime()
        except Exception as e:
            logger.error(f"Time parsing error: {e}")
            return False # エラー時は安全のために取引拒否
        # ＝＝＝ ▲ 修正 ここまで ▲ ＝＝＝

        # 防壁1〜4 (重複・Kill Switch・マシンガン・クールダウン)
        if any(p["pair"] == pair for p in self.positions): return False
        if pair in self.lockout_until and current_time < self.lockout_until[pair]: return False
        if pair in self.last_trade_time:
            if (current_time - self.last_trade_time[pair]).total_seconds() < (14 * 60): return False

        risk_config = cfg.get_sync("risk_management", {})
        cooldown_min = risk_config.get("cooldown_minutes", 30)
        if pair in self.last_close_time and pair in self.last_close_direction:
            if (current_time - self.last_close_time[pair]).total_seconds() < (cooldown_min * 60) and direction == self.last_close_direction[pair]:
                return False

        # ＝＝＝ ▼ 修正：第5の防護壁（事前パース済みデータで瞬時に判定） ▼ ＝＝＝
        try:
            jst_time_dt = current_time.astimezone(pytz.timezone('Asia/Tokyo'))
            jst_time = jst_time_dt.time()
            jst_weekday = jst_time_dt.weekday()

            if self.time_filters:
                # 週末停止判定
                if jst_weekday == 4 and jst_time_dt.hour >= self.friday_stop_hour: return False
                if jst_weekday in (5, 6): return False

                # 魔の時間帯判定
                if self.f_start_t and self.f_end_t:
                    if self.f_start_t <= self.f_end_t:
                        if self.f_start_t <= jst_time <= self.f_end_t: return False
                    else: # 日またぎ設定
                        if jst_time >= self.f_start_t or jst_time <= self.f_end_t: return False
        except Exception as e:
            logger.error(f"Time filter logic error: {e}")
            return False
        # ＝＝＝ ▲ 修正 ここまで ▲ ＝＝＝

        return True

    # core/virtual_trade.py の open_position メソッドを丸ごと上書き
    def open_position(self, pair: str, direction: str, lot_size: int, entry_price: float, sl_price: float, tp_price: float, context: dict = None, current_time: datetime = None):
        """ポジションをオープンし、防衛システムに記録を残す"""
        if lot_size <= 0: return

        # 【修正】引数で時刻が渡されればそれを使い、なければ現在時刻（本番用）を使う
        eval_time = current_time if current_time else datetime.now(timezone.utc)

        if not self.can_open_position(pair, eval_time, direction):
            return

        pos_id = str(uuid.uuid4())[:8]
        position = {
            "id": pos_id,
            "pair": pair,
            "direction": direction,
            "lot_size": lot_size,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "initial_sl": sl_price,
            "tp_price": tp_price,
            "open_time": eval_time, # ここも eval_time に変更
            "trail_dist": context.get("trail", 0.0) if context else 0.0,
            "context": context or {}
        }
        self.positions.append(position)

        self.last_trade_time[pair] = eval_time # ここも eval_time に変更
        self.latest_mid_prices[pair] = entry_price

        logger.info(f"Virtual Position Opened: {direction} {pair}, Units: {lot_size}, Price: {entry_price:.5f}")
        if not self.is_backtest:
            self._save_open_positions_json()

    # 1. 決済処理の共通口を修正 (close_position -> _close_position も同様に引数を拡張)
    async def close_position(self, trade_id: str, reason: str = "Doten Reverse", current_time: datetime = None):
        """指定されたIDのポジションを強制決済する"""
        eval_time = current_time if current_time else datetime.now(timezone.utc)
        for pos in self.positions[:]:
            if pos["id"] == trade_id:
                mid_price = self.latest_mid_prices.get(pos["pair"], pos["entry_price"])
                await self._close_position(pos, mid_price, reason, eval_time)
                break

    async def _close_position(self, pos: Dict, exit_price: float, reason: str, eval_time: datetime):
        """ポジションを閉じ、正しいシミュレーション時刻を記録する"""
        price_diff = (exit_price - pos["entry_price"]) if pos["direction"] == "long" else (pos["entry_price"] - exit_price)
        
        # 修正: USDストレートの損益をJPYに正しく換算する
        from core.risk import conversion_provider
        pair = pos["pair"]
        clean_pair = pair.replace("_", "")
        quote_currency = clean_pair[3:] if len(clean_pair) == 6 else "USD"
        
        # クロス円ならレートは1.0、それ以外は対円レートを取得
        if "JPY" in pair:
            exchange_rate = 1.0
        else:
            exchange_rate = conversion_provider.rates.get(quote_currency, 150.0)
            
        profit_jpy = price_diff * pos["lot_size"] * exchange_rate
        
        self.balance += profit_jpy
        self.positions.remove(pos)

        # 【修正】評価時刻(eval_time)を正しく記録
        self.last_close_time[pos["pair"]] = eval_time
        self.last_close_direction[pos["pair"]] = pos["direction"]

        # 連敗ストッパー・ロックアウトの判定にも eval_time を使用
        pair = pos["pair"]
        if profit_jpy < 0:
            self.consecutive_losses[pair] = self.consecutive_losses.get(pair, 0) + 1
            if self.consecutive_losses[pair] >= 2:
                logger.warning(f"🛑 [LOSS LIMIT] {pair} 2連敗を検知。12時間ロックアウトします。")
                self.lockout_until[pair] = eval_time + timedelta(hours=12)
                self.consecutive_losses[pair] = 0
        else:
            self.consecutive_losses[pair] = 0

        logger.info(f"Virtual Position Closed: {pos['pair']} ({reason}), Profit: {profit_jpy:,.0f} JPY")

        log_data = {
                "timestamp": eval_time.isoformat(),
                "entry_time": pos["open_time"].isoformat(),
                "pair": pos["pair"],
                "direction": pos["direction"],
                "strategy": pos["context"].get("strategy", "unknown"),
                "ai_score": pos["context"].get("score", 0.0),
                "spread_pips": pos["context"].get("spread_pips", 0.0),  # 👈 追加
                "lot_size": pos["lot_size"],
                "entry_price": pos["entry_price"],
                "exit_price": exit_price,
                "profit_amount": profit_jpy,
                "new_balance": self.balance,
                "reason": reason
            }

        df = pd.DataFrame([log_data])
        
        # バックテスト用の実績データとして保持
        self.closed_positions_history.append(log_data)
        
        if not self.is_backtest:
            await append_csv(cfg, f"virtual_trade_log_{eval_time:%Y%m%d}.csv", df)
            self._save_open_positions_json()

    async def update_trade_stop_loss(self, trade_id: str, new_sl_price: float):
        """本番ロジックからのTSL（ストップロス引き上げ）指示を反映する"""
        for pos in self.positions:
            if pos["id"] == trade_id:
                old_sl = pos["sl_price"]
                pos["sl_price"] = new_sl_price
                logger.info(f"🛡️ [VirtualBroker] Position {trade_id} SL updated: {old_sl:.5f} -> {new_sl_price:.5f}")
                if not self.is_backtest:
                    self._save_open_positions_json()  # 👈 即時ダッシュボード同期
                break

    async def update_market_price(self, pair: str, bid: float, ask: float, current_time: datetime = None):
        """
        最新価格でポジションを評価し、Kill Switch、Flash Exit、および決済判定を行う
        """
        mid_price = (bid + ask) / 2.0
        eval_time = current_time if current_time else datetime.now(timezone.utc)
        self.latest_mid_prices[pair] = mid_price

        self._monitor_kill_switch(pair, mid_price, eval_time)

        for pos in self.positions[:]:
            if pos["pair"] != pair: continue

            closed = False
            reason = ""
            exit_price = 0.0

            # 🚨 0. 急激な逆行（Flash Exit）の優先判定
            if self._check_flash_adverse_move(pos, mid_price, eval_time):
                closed = True
                reason = "Flash Exit"
                # 実勢の成行約定価格（LongならBid、ShortならAsk）で決済
                exit_price = bid if pos["direction"] == "long" else ask

            # --- 1. 通常の決済判定（TP or 引き上げられたSLにヒットしたか） ---
            elif pos["direction"] == "long":
                if bid <= pos["sl_price"]:
                    closed = True
                    reason = "SL" if pos["sl_price"] == pos["initial_sl"] else "TSL Hit"
                    exit_price = pos["sl_price"]
                elif bid >= pos["tp_price"]:
                    closed = True
                    reason = "TP"
                    exit_price = pos["tp_price"]
            elif pos["direction"] == "short":
                if ask >= pos["sl_price"]:
                    closed = True
                    reason = "SL" if pos["sl_price"] == pos["initial_sl"] else "TSL Hit"
                    exit_price = pos["sl_price"]
                elif ask <= pos["tp_price"]:
                    closed = True
                    reason = "TP"
                    exit_price = pos["tp_price"]

            # --- 2. 週末(金曜NYクローズ) 判定 ---
            if not closed:
                if eval_time.weekday() == 4 and eval_time.hour >= 21:
                    closed = True
                    reason = "Weekend Close"
                    exit_price = mid_price

            # 決済実行
            if closed:
                await self._close_position(pos, exit_price, reason, eval_time)

        if len(self.positions) > 0 and not self.is_backtest:
            self._save_open_positions_json()
 
    def _save_open_positions_json(self):
        """現在保有している全ポジションのリストをファイル出力する"""
        if self.is_backtest:
            return

        try:
            data_dir = cfg.project_root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            file_path = data_dir / "virtual_open_positions.json"

            export_list = []
            for pos in self.positions:
                mid = self.latest_mid_prices.get(pos["pair"], pos["entry_price"])
                pnl = (mid - pos["entry_price"]) * pos["lot_size"] if pos["direction"] == "long" else \
                      (pos["entry_price"] - mid) * pos["lot_size"]

                export_list.append({
                    "id": pos["id"],
                    "pair": pos["pair"],
                    "direction": pos["direction"],
                    "lot_size": pos["lot_size"],
                    "entry_price": pos["entry_price"],
                    "sl_price": pos["sl_price"],
                    "tp_price": pos["tp_price"],
                    "open_time": pos["open_time"].isoformat(),
                    "unrealized_pnl": round(pnl, 2)
                })

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(export_list, f, indent=4, ensure_ascii=False)

        except Exception as e:
            logger.error(f"Failed to save open positions JSON: {e}")

    def _check_flash_adverse_move(self, pos: dict, mid_price: float, current_time: datetime) -> bool:
        """
        【急変対策】直近60秒間で急激な逆行（5.0 pips以上）が発生した場合、
        通常のSLを待たずに即座に緊急脱出（Flash Exit）フラグを立てる。
        """
        pair = pos["pair"]
        history = self.price_history.get(pair, [])
        cutoff_60s = current_time - timedelta(seconds=60)

        # 直近60秒以内のティック履歴を抽出
        recent_ticks = [h[1] for h in history if h[0] >= cutoff_60s]

        if len(recent_ticks) < 2:
            return False

        pip_scale = 0.01 if "JPY" in pair else 0.0001
        threshold_pips = 5.0  # 1分間に 5.0 pips 以上の急激逆行で発動

        if pos["direction"] == "long":
            peak_price = max(recent_ticks)
            adverse_drop_pips = (peak_price - mid_price) / pip_scale
            if adverse_drop_pips >= threshold_pips:
                logger.critical(f"🚨 [FLASH EXIT] {pair} Long 急落検知! 60秒間で {adverse_drop_pips:.1f} pips 逆行。緊急脱出を実行します。")
                return True
        elif pos["direction"] == "short":
            valley_price = min(recent_ticks)
            adverse_spike_pips = (mid_price - valley_price) / pip_scale
            if adverse_spike_pips >= threshold_pips:
                logger.critical(f"🚨 [FLASH EXIT] {pair} Short 急騰検知! 60秒間で {adverse_spike_pips:.1f} pips 逆行。緊急脱出を実行します。")
                return True

        return False