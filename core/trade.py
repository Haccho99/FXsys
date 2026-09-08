"""
trade.py (v15.1 - Added All Trades Getter)
"""
from __future__ import annotations
import asyncio
from typing import Optional, Dict, Any
import pandas as pd
import oandapyV20
import oandapyV20.endpoints.trades as trades
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.accounts as accounts
import oandapyV20.endpoints.positions as positions
from oandapyV20.exceptions import V20Error
import math


from core import cfg
from core.logger import get_logger, log_error
from .oanda_api import get_active_account_id, get_active_token, get_active_environment

logger = get_logger(cfg, "trade")

_api_client_singleton: Optional[oandapyV20.API] = None

def get_oanda_api_client() -> oandapyV20.API:
    """oandapyV20.APIのシングルトンインスタンスを返す。"""
    global _api_client_singleton
    if _api_client_singleton is None:
        _api_client_singleton = oandapyV20.API(
            access_token=get_active_token(),
            environment=get_active_environment()
        )
    return _api_client_singleton

class TradeExecutor:
    def __init__(self, dry_run: bool = False):
        self.account_id = get_active_account_id()
        self.dry_run = dry_run
        self._open_trades: Dict[str, list[Dict[str, Any]]] = {}
        self.account_summary: Dict[str, Any] = {}

    async def get_account_summary(self) -> Dict:
        """OANDA APIから最新の口座情報を取得し、キャッシュする。"""
        if self.dry_run:
            return {"balance": 1000000, "margin_used": 10000, "unrealized_pl": 5000, "pl": 150000}
        
        try:
            loop = asyncio.get_running_loop()
            api = get_oanda_api_client()
            r = accounts.AccountSummary(accountID=self.account_id)
            response = await loop.run_in_executor(None, lambda: api.request(r))
            summary = response.get('account', {})
            self.account_summary = {
                "balance": float(summary.get('balance', 0)),
                "margin_used": float(summary.get('marginUsed', 0)),
                "unrealized_pl": float(summary.get('unrealizedPL', 0)),
                "pl": float(summary.get('pl', 0))
            }
            logger.debug("Account summary updated.", summary=self.account_summary)
            return self.account_summary
        except Exception as e:
            await log_error(logger, "get_account_summary", e)
            return {}

    async def initialize_state(self):
        """OANDAのオープンなポジションと内部状態を同期する。"""
        logger.info("Initializing trade state...")
        await self.get_account_summary()
        if self.dry_run:
            logger.warning("Dry run mode is enabled. Skipping OANDA state synchronization.")
            return
        try:
            loop = asyncio.get_running_loop()
            api = get_oanda_api_client()
            r = trades.OpenTrades(accountID=self.account_id)
            await loop.run_in_executor(None, lambda: api.request(r))
            
            self._open_trades.clear()
            for trade_data in r.response.get('trades', []):
                instrument = trade_data['instrument']
                units = int(trade_data['currentUnits'])
                trade_info = {
                    "id": trade_data['id'],
                    "pair": instrument,
                    "side": "long" if units > 0 else "short",
                    "entry_price": float(trade_data['price']),
                    "sl_price": float(trade_data.get('stopLossOrder', {}).get('price', 0.0)),
                    "tp_price": float(trade_data.get('takeProfitOrder', {}).get('price', 0.0)),
                    "current_units": abs(units),
                    "partial_close_executed": False
                }
                self._open_trades.setdefault(instrument, []).append(trade_info)
            
            logger.info("Successfully synchronized state with OANDA.", open_trades=self._open_trades)
        except Exception as e:
            await log_error(logger, "initialize_state", e)

    def get_open_trades(self, pair: str) -> list[Dict[str, Any]]:
        return self._open_trades.get(pair, [])

    def get_all_open_trades(self) -> Dict[str, list[Dict[str, Any]]]:
        """保有している全てのオープンポジションの辞書を返す。"""
        return self._open_trades

    async def open_position(self, signal: Dict):
        pair = signal["pair"]
        side_unit = signal["units"] if signal["signal"] == "long" else -signal["units"]
        if self.dry_run:
            logger.info(f"[DRY RUN] Order to {signal['signal']} {pair} not sent.", signal=signal)
            trade_id = f"dry_run_{pd.Timestamp.now('UTC').timestamp()}"
            self._open_trades.setdefault(pair, []).append({
                "id": trade_id, "pair": pair, "side": signal["signal"],
                "entry_price": signal["entry_price"], "sl_price": signal["sl"],
                "tp_price": signal["tp"], "trail_dist": signal.get("trail"),
                "current_units": signal["units"],
                "partial_close_executed": False
            })
            return
        data = { "order": {
                "type": "MARKET", "instrument": pair, "units": str(side_unit),
                "stopLossOnFill": {"price": f"{signal['sl']:.5f}"},
                "takeProfitOnFill": {"price": f"{signal['tp']:.5f}"}
        }}
        r = orders.OrderCreate(accountID=self.account_id, data=data)
        try:
            loop = asyncio.get_running_loop()
            api = get_oanda_api_client()
            response = await loop.run_in_executor(None, lambda: api.request(r))
            logger.info("OrderCreate response", response=response)
            await self.initialize_state()
        except V20Error as e:
            await log_error(logger, "open_position", e, signal=signal)

    async def update_trade_stop_loss(self, trade_id: str, new_sl_price: float):
        if self.dry_run:
            logger.info(f"[DRY RUN] SL update for trade {trade_id} to {new_sl_price} not sent.")
            for pair_trades in self._open_trades.values():
                for trade in pair_trades:
                    if trade['id'] == trade_id:
                        trade['sl_price'] = new_sl_price; return
            return
        data = {"stopLoss": {"price": f"{new_sl_price:.5f}", "timeInForce": "GTC"}}
        r = trades.TradeOrders(accountID=self.account_id, tradeID=trade_id, data=data)
        try:
            loop = asyncio.get_running_loop()
            api = get_oanda_api_client()
            await loop.run_in_executor(None, lambda: api.request(r))
            logger.info(f"Successfully sent SL update request for trade {trade_id}.")
            for pair_trades in self._open_trades.values():
                for trade in pair_trades:
                    if trade['id'] == trade_id:
                        trade['sl_price'] = new_sl_price; return
        except V20Error as e:
            await log_error(logger, "update_trade_stop_loss", e, trade_id=trade_id)

    async def partial_close_position(self, trade_id: str, units_to_close: int):
        logger.info(f"Attempting to partially close {units_to_close} units for trade {trade_id}.")
        if self.dry_run:
            logger.warning(f"[DRY RUN] Partial close for trade {trade_id} not sent to API.")
            for pair_trades in self._open_trades.values():
                for trade in pair_trades:
                    if trade['id'] == trade_id:
                        remaining_units = trade.get('current_units', 0) - units_to_close
                        if remaining_units > 0:
                            trade['current_units'] = remaining_units
                            trade['partial_close_executed'] = True
                            logger.info(f"[DRY RUN] Internal state for {trade_id} updated. New units: {remaining_units}, Flag set to True.")
                        else:
                            pair_trades.remove(trade)
                            logger.info(f"[DRY RUN] Trade {trade_id} fully closed. Removing from state.")
                        return
            return
        if units_to_close <= 0: return
        data = {"units": str(units_to_close)}
        r = trades.TradeClose(accountID=self.account_id, tradeID=trade_id, data=data)
        try:
            loop = asyncio.get_running_loop()
            api = get_oanda_api_client()
            await loop.run_in_executor(None, lambda: api.request(r))
            logger.info(f"Partial close request for trade {trade_id} successful.")
            details_req = trades.TradeDetails(accountID=self.account_id, tradeID=trade_id)
            details_resp = await loop.run_in_executor(None, lambda: api.request(details_req))
            remaining_units = int(details_resp['trade']['currentUnits'])
            logger.info(f"Trade {trade_id} details updated. Remaining units: {remaining_units}")
            for pair_trades in self._open_trades.values():
                for trade in pair_trades:
                    if trade['id'] == trade_id:
                        trade['current_units'] = abs(remaining_units)
                        trade['partial_close_executed'] = True
                        logger.info(f"Local state for trade {trade_id} updated to {abs(remaining_units)} units, Flag set to True.")
                        return
        except V20Error as e:
            if "NO_SUCH_TRADE" in str(e):
                logger.info(f"Trade {trade_id} was fully closed. Removing from local state.")
                for pair, pair_trades in self._open_trades.items():
                    self._open_trades[pair] = [t for t in pair_trades if t['id'] != trade_id]
                    return
            else:
                await log_error(logger, "partial_close_position", e, trade_id=trade_id)

    async def close_trade(self, trade_id: str) -> bool:
        """指定したTrade IDのポジションを全量完全決済する"""
        logger.info(f"Closing trade {trade_id} completely.")
        if self.dry_run:
            logger.info(f"[DRY RUN] Trade {trade_id} completely closed (simulated).")
            for pair, pair_trades in self._open_trades.items():
                self._open_trades[pair] = [t for t in pair_trades if t['id'] != trade_id]
            return True

        data = {"units": "ALL"}
        r = trades.TradeClose(accountID=self.account_id, tradeID=trade_id, data=data)
        try:
            loop = asyncio.get_running_loop()
            api = get_oanda_api_client()
            await loop.run_in_executor(None, lambda: api.request(r))
            logger.info(f"Successfully closed trade {trade_id}.")
            
            # ローカル状態から削除
            for pair, pair_trades in self._open_trades.items():
                self._open_trades[pair] = [t for t in pair_trades if t['id'] != trade_id]
            return True
        except V20Error as e:
            await log_error(logger, "close_trade", e, trade_id=trade_id)
            return False

    async def close_all_positions(self) -> bool:
        """保有中の全ポジションを一括で成行決済する（パニックボタン用）"""
        logger.warning("🚨 EMERGENCY: Closing ALL open positions across all instruments!")
        if self.dry_run:
            logger.info("[DRY RUN] ALL trades completely closed (simulated).")
            self._open_trades.clear()
            return True

        open_pairs = list(self._open_trades.keys())
        if not open_pairs:
            return True

        api = get_oanda_api_client()
        loop = asyncio.get_running_loop()

        async def _close_pair(pair: str):
            # OANDAの400エラーを防ぐため、存在するsideのみALLを指定する
            trades_for_pair = self._open_trades.get(pair, [])
            data = {}
            for t in trades_for_pair:
                if t['side'] == 'long':
                    data['longUnits'] = "ALL"
                elif t['side'] == 'short':
                    data['shortUnits'] = "ALL"
            
            if not data:
                return

            r = positions.PositionClose(accountID=self.account_id, instrument=pair, data=data)
            try:
                await loop.run_in_executor(None, lambda: api.request(r))
            except V20Error as e:
                await log_error(logger, f"close_all_positions_{pair}", e)

        # 全ペアを非同期並列で一斉決済
        await asyncio.gather(*[_close_pair(pair) for pair in open_pairs])
        await self.initialize_state()  # 状態を完全再同期
        return True

_executor_singleton: Optional[TradeExecutor] = None
_executor_lock = asyncio.Lock()

async def get_executor(dry_run: bool | None = None) -> TradeExecutor:
    global _executor_singleton
    if _executor_singleton is None:
        async with _executor_lock:
            if _executor_singleton is None:
                is_dry_run = dry_run if dry_run is not None else cfg.get_sync("system.dry_run", False)
                instance = TradeExecutor(dry_run=is_dry_run)
                await instance.initialize_state()
                _executor_singleton = instance
    return _executor_singleton