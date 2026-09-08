"""
core/hwm_tracker.py - 過去最高残高（High Water Mark）管理 & アンチ・マーチンゲール
ドローダウンブレーキ算出モジュール
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from core import cfg, PROJECT_ROOT
from core.logger import get_logger
from core.redis_client import create_redis_client_sync

logger = get_logger(cfg, "hwm_tracker")

REDIS_HWM_KEY = "system:hwm_state"
LOCAL_HWM_FILE = PROJECT_ROOT / "data" / "hwm_state.json"


class HWMTracker:
    """
    口座の過去最高残高（High Water Mark: HWM）を永続的に記録・監視し、
    現在のドローダウン（DD）率に基づいてアンチ・マーチンゲール方式の
    ロット縮小ブレーキ倍率およびキルスイッチを算出する専用マネージャー。
    """

    def __init__(self, custom_cfg=None):
        self.cfg = custom_cfg or cfg
        self.log = get_logger(self.cfg, "hwm_tracker")
        self.redis_client = None
        self._init_redis()
        self._ensure_data_dir()

    def _init_redis(self):
        """同期Redisクライアントの初期化（失敗時はローカルファイルへフォールバック）"""
        try:
            self.redis_client = create_redis_client_sync(self.cfg)
        except Exception as e:
            self.log.warning(f"Redis initialization failed in HWMTracker: {e}. Falling back to local JSON.")
            self.redis_client = None

    def _ensure_data_dir(self):
        """ローカル保存先ディレクトリの存在確認"""
        LOCAL_HWM_FILE.parent.mkdir(parents=True, exist_ok=True)

    def _get_default_state(self) -> Dict[str, Any]:
        """初期デフォルト状態の生成"""
        init_balance = float(self.cfg.get_sync("trading.balance_init", 1_000_000.0))
        now_iso = datetime.now(timezone.utc).isoformat()
        return {
            "high_water_mark": init_balance,
            "peak_time": now_iso,
            "last_updated": now_iso,
            "initial_balance": init_balance
        }

    def load_state(self) -> Dict[str, Any]:
        """
        HWM状態をロードする。
        1. Redis からの取得を試行
        2. 失敗時はローカル JSON (data/hwm_state.json) から取得
        3. いずれも存在しない場合はデフォルト値を生成して保存
        """
        if self.redis_client:
            try:
                raw_data = self.redis_client.get(REDIS_HWM_KEY)
                if raw_data:
                    return json.loads(raw_data)
            except Exception as e:
                self.log.warning(f"Failed to read HWM state from Redis: {e}. Falling back to file.")

        if LOCAL_HWM_FILE.exists():
            try:
                with open(LOCAL_HWM_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                self.log.error(f"Failed to read HWM state from local JSON: {e}")

        default_state = self._get_default_state()
        self.save_state(default_state)
        return default_state

    def save_state(self, state: Dict[str, Any]) -> bool:
        """
        HWM状態を永続化する（RedisおよびローカルJSONの両方へ書き込み）。
        """
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        success = True

        if self.redis_client:
            try:
                self.redis_client.set(REDIS_HWM_KEY, json.dumps(state, ensure_ascii=False))
            except Exception as e:
                self.log.warning(f"Failed to save HWM state to Redis: {e}")
                success = False

        try:
            self._ensure_data_dir()
            temp_file = LOCAL_HWM_FILE.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=4, ensure_ascii=False)
            temp_file.replace(LOCAL_HWM_FILE)
        except Exception as e:
            self.log.error(f"Failed to save HWM state to local JSON file: {e}")
            success = False

        return success

    def update_and_get_hwm(self, current_equity: float) -> float:
        """
        現在の有効証拠金（Equity）を受け取り、過去最高残高を更新して返す。
        """
        if current_equity <= 0:
            state = self.load_state()
            return float(state.get("high_water_mark", 1_000_000.0))

        state = self.load_state()
        current_hwm = float(state.get("high_water_mark", 0.0))

        if current_equity > current_hwm:
            old_hwm = current_hwm
            state["high_water_mark"] = current_equity
            state["peak_time"] = datetime.now(timezone.utc).isoformat()
            self.save_state(state)
            self.log.info(
                f"🎉 [HWM NEW PEAK] High Water Mark updated: {old_hwm:,.0f} -> {current_equity:,.0f} JPY"
            )
            return current_equity

        return current_hwm

    def get_current_drawdown_pct(self, current_equity: float) -> float:
        """
        現在の有効証拠金から、最高残高（HWM）に対するドローダウン率（0.0〜1.0）を算出する。
        """
        hwm = self.update_and_get_hwm(current_equity)
        if hwm <= 0 or current_equity >= hwm:
            return 0.0
        return (hwm - current_equity) / hwm

    def get_drawdown_multiplier(self, current_equity: float) -> float:
        """
        config.json の anti_martingale.drawdown_brakes に基づき、
        現在のドローダウン率に応じたロット倍率（lot_multiplier）を返す。
        """
        anti_mart_cfg = self.cfg.get_sync("anti_martingale", {})
        if not anti_mart_cfg.get("enabled", True):
            return 1.0

        hwm = self.update_and_get_hwm(current_equity)
        if hwm <= 0 or current_equity <= 0:
            return 1.0

        dd_pct = self.get_current_drawdown_pct(current_equity)

        brakes = anti_mart_cfg.get("drawdown_brakes", [
            {"dd_pct": 0.05, "lot_multiplier": 0.75},
            {"dd_pct": 0.10, "lot_multiplier": 0.50},
            {"dd_pct": 0.15, "lot_multiplier": 0.0, "kill_switch": True}
        ])

        sorted_brakes = sorted(brakes, key=lambda x: x.get("dd_pct", 0.0), reverse=True)

        for tier in sorted_brakes:
            threshold = tier.get("dd_pct", 0.0)
            if dd_pct >= threshold:
                multiplier = tier.get("lot_multiplier", 1.0)
                is_kill_switch = tier.get("kill_switch", False) or (multiplier == 0.0)

                if is_kill_switch:
                    self.log.critical(
                        f"🚨 [ANTI-MARTINGALE KILL-SWITCH] Drawdown reached {dd_pct*100:.2f}% "
                        f"(Threshold: {threshold*100:.1f}%). All new trades STOPPED!"
                    )
                    return 0.0
                else:
                    self.log.warning(
                        f"⚠️ [ANTI-MARTINGALE BRAKE] Drawdown at {dd_pct*100:.2f}% (>= "
                        f"{threshold*100:.1f}%). Applying lot multiplier: {multiplier}x"
                    )
                    return multiplier

        return 1.0

    def reset_hwm(self, new_balance: float) -> float:
        """手動介入や追加入金・出金時にHWMをリセットする"""
        now_iso = datetime.now(timezone.utc).isoformat()
        state = {
            "high_water_mark": float(new_balance),
            "peak_time": now_iso,
            "last_updated": now_iso,
            "initial_balance": float(new_balance)
        }
        self.save_state(state)
        self.log.info(f"🔄 [HWM RESET] High Water Mark manually reset to: {new_balance:,.0f} JPY")
        return float(new_balance)

hwm_tracker = HWMTracker()