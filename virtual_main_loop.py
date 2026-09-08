"""
virtual_main_loop.py - 仮想フォワードテスト用メインループ (v4 - Strict Timing, H1 Trend, Heartbeat)
"""
import asyncio
import sys
from datetime import datetime, timezone
import polars as pl
import pytz

from core import cfg
from core.logger import get_logger, log_error
from core.data import fetch_candles, get_spread
from core.monitor import GuardCenter
from core.signal_module import SignalGenerator
from core.virtual_trade import VirtualBroker
from core.risk import position_units

logger = get_logger(cfg, "virtual_main")

async def virtual_trade_cycle(pair: str, signal_gen: "SignalGenerator", broker: "VirtualBroker", guard: "GuardCenter"):
    """仮想環境での取引サイクルを実行する。"""
    try:
        import polars as pl  

        # 〇 執行タイミングの厳格化
        if not hasattr(virtual_trade_cycle, "last_exec_block"):
            virtual_trade_cycle.last_exec_block = {}

        now = datetime.now(timezone.utc)
        now_jst = now.astimezone(pytz.timezone('Asia/Tokyo'))
        current_block = now.minute // 15

        if now.minute % 15 <= 2:
            if virtual_trade_cycle.last_exec_block.get(pair) != current_block:
                virtual_trade_cycle.last_exec_block[pair] = current_block
            else:
                return 
        else:
            return 

        logger.info(f"[{pair}] [DEBUG-LOOP] >>> Start Cycle Evaluation at JST {now_jst.strftime('%Y-%m-%d %H:%M:%S')} <<<")

        # 1. ブロックチェック (GuardCenter)
        is_blocked, reason = await guard.is_blocked(pair)
        if is_blocked:
            logger.info(f"[{pair}] [DEBUG-LOOP] Skipped: GuardCenter blocked (Reason: {reason})")
            return

        # 2. スプレッド取得 ＆ 【防御壁③】スプレッド超過ガード
        spread_pips = await get_spread(pair)
        spread_threshold = cfg.get_sync("risk_management.volatility_filter.spread_threshold_pips", 3.0)
        if spread_pips > spread_threshold:
            logger.info(f"[{pair}] [DEBUG-LOOP] Skipped: Spread {spread_pips:.2f} pips > threshold {spread_threshold:.2f} pips")
            return
        else:
            logger.info(f"[{pair}] [DEBUG-LOOP] Spread OK: {spread_pips:.2f} pips (Threshold: {spread_threshold:.2f})")

        # 3. 【防御壁④】魔の時間帯 ＆ 週末クローズ ガード
        time_filters = cfg.get_sync("system.time_filters", {})
        f_start_str = time_filters.get("forbidden_start", "01:00")
        f_end_str = time_filters.get("forbidden_end", "09:00")
        friday_stop = int(time_filters.get("friday_stop_hour", 20))
        
        # 時刻のパース
        from datetime import datetime as dt_sys
        f_start = dt_sys.strptime(f_start_str, "%H:%M").time()
        f_end = dt_sys.strptime(f_end_str, "%H:%M").time()
        curr_time = now_jst.time()
        
        is_forbidden = False
        # 日常の魔の時間帯ガード（例: 01:00〜09:00）
        if f_start <= f_end:
            if f_start <= curr_time <= f_end: is_forbidden = True
        else: # 22:00〜05:00 のような日またぎ設定用
            if curr_time >= f_start or curr_time <= f_end: is_forbidden = True
            
        # 金曜日(weekday==4) の指定時間以降は新規エントリー停止
        if now_jst.weekday() == 4 and now_jst.hour >= friday_stop:
            is_forbidden = True
        # 土曜日(weekday==5) も終日停止
        if now_jst.weekday() == 5:
            is_forbidden = True

        if is_forbidden:
            logger.info(f"[{pair}] [DEBUG-LOOP] Skipped: Forbidden time ({f_start_str}-{f_end_str}) or weekend")
            return # 魔の時間帯・週末はシグナル生成自体をスキップ（決済のみ継続）

        # 4. データ取得（M15足）
        df_m15 = await fetch_candles(pair, gran="M15", count=300) 
        if df_m15 is None or df_m15.is_empty() or len(df_m15) < 250:
            logger.info(f"[{pair}] [DEBUG-LOOP] Skipped: Insufficient candle count ({len(df_m15) if df_m15 is not None else 0})")
            return

        # 5. 最新センチメント取得
        sentiment_score = await guard.get_latest_sentiment(pair)
        logger.info(f"[{pair}] [DEBUG-LOOP] Sentiment score: {sentiment_score:.2f}, M15 Bars: {len(df_m15)}")

        # 6. シグナル生成
        signal = await signal_gen.generate_signal(
            pair=pair, df_m15=df_m15, spread_pips=spread_pips, sentiment_score=sentiment_score
        )

        # ▼ 新規追加: シグナル候補をRedisへ送信 (ダッシュボード用) ▼
        from core.redis_client import create_redis_client
        import json
        redis_client = await create_redis_client(cfg, logger)
        if redis_client:
            if signal:
                candidate_data = {
                    "pair": pair,
                    "direction": signal.get("signal", "unknown"),
                    "price": signal.get("entry_price", df_m15["close"][-1] if not df_m15.is_empty() else 0),
                    "conditions": f"Score={signal.get('score', 0.0):.2f}",
                    "score": signal.get("score", 0.0),
                    "timestamp": now_jst.strftime("%Y-%m-%d %H:%M:%S")
                }
                await redis_client.hset("signal_candidates", pair, json.dumps(candidate_data))
            else:
                # シグナルが消滅した場合は候補から削除
                await redis_client.hdel("signal_candidates", pair)
            await redis_client.close()
        # ▲ 新規追加 ここまで ▲

        if signal:
            lot_units = signal.get("units", 0)

            if lot_units > 0:
                logger.warning(f"[{pair}] [DEBUG-LOOP] ★★★ Virtual Signal Generated: {pair} {signal['signal']}, Score: {signal['score']:.2f}, Units: {lot_units} ★★★")
                
                # signalモジュールが計算した完璧な tp と sl を、そのままブローカーに渡す
                broker.open_position(
                    pair=pair, 
                    direction=signal["signal"], 
                    lot_size=lot_units,
                    entry_price=signal["entry_price"], 
                    sl_price=signal["sl"],    # 厨房から来た正しいSL価格
                    tp_price=signal["tp"],    # 厨房から来た正しいTP価格
                    context=signal
                )
            else:
                logger.info(f"[{pair}] [DEBUG-LOOP] Signal generated but lot_units <= 0")
        else:
            logger.info(f"[{pair}] [DEBUG-LOOP] No signal generated for this bar")

    except Exception as e:
        import traceback
        logger.error(f"[{pair}] [DEBUG-LOOP-ERROR] Exception in virtual_trade_cycle: {e}\n{traceback.format_exc()}")

async def virtual_price_stream_task(broker: VirtualBroker):
    """リアルタイム価格をVirtualBrokerに供給し、約定を監視する。"""
    symbols = cfg.get_sync("trading.symbols", ["USD_JPY"])

    async def stream_for_pair(pair):
        while True:
            try:
                async for price_data in pricing_stream_full(pair):
                    await broker.update_market_price(pair, price_data["bid"], price_data["ask"])
            except Exception as e:
                await log_error(logger, f"virtual_stream_{pair}", e)
                await asyncio.sleep(5)

    tasks = [stream_for_pair(pair) for pair in symbols]
    await asyncio.gather(*tasks)

async def pricing_stream_full(pair: str):
    """Bid/Askを含む価格ストリーム（空配列・非取引ステータス防御付き）"""
    import httpx
    import json
    from core.oanda_api import get_active_token, build_stream_url, get_active_account_id

    account_id = get_active_account_id()
    if not account_id:
        raise ValueError("Active account ID not found. Check system management settings.")

    # URL生成時のペア文字列整形 (例: USD_JPY -> USD_JPY)
    url = build_stream_url(f"accounts/{account_id}/pricing/stream?instruments={pair.replace('/', '_')}")
    headers = {"Authorization": f"Bearer {get_active_token()}"}

    async with httpx.AsyncClient(headers=headers, timeout=None) as client:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if line:
                    line = line.strip()
                    if line.startswith('{"type":"PRICE"'):
                        j = json.loads(line)

                        # 🛡️ 防御節 1: 非取引ステータス（市場休止中など）をスキップ
                        if j.get("status") == "non-tradeable" or j.get("tradeable") is False:
                            continue

                        # 🛡️ 防御節 2: bids / asks 配列の存在と要素数をチェック (IndexError 完全防止)
                        bids = j.get("bids", [])
                        asks = j.get("asks", [])
                        if not bids or not asks or len(bids) == 0 or len(asks) == 0:
                            continue

                        yield {
                            "bid": float(bids[0]["price"]),
                            "ask": float(asks[0]["price"])
                        }

# ＝＝＝ ▼ 修正箇所1：アダプターへのメソッド追加 ▼ ＝＝＝
class VirtualExecutorAdapter:
    def __init__(self, broker: VirtualBroker):
        self.broker = broker
    async def get_account_summary(self):
        return {"balance": self.broker.balance}
    def get_open_trades(self, pair: str):
        return [p for p in self.broker.positions if p["pair"] == pair]
    def get_all_open_trades(self):
        result = {}
        for p in self.broker.positions:
            p_pair = p["pair"]
            if p_pair not in result: result[p_pair] = []
            result[p_pair].append(p)
        return result
    
    # 【新規追加】シグナルモジュールからのSL更新指示をブローカーに流す
    async def update_trade_stop_loss(self, trade_id: str, new_sl_price: float):
        await self.broker.update_trade_stop_loss(trade_id, new_sl_price)
# ＝＝＝ ▲ 修正箇所1 ▲ ＝＝＝

# ＝＝＝ ▼ 修正箇所2：価格ストリームへのTSL連結 ▼ ＝＝＝
# 引数に signal_gen を追加します
async def virtual_price_stream_task(broker: VirtualBroker, signal_gen: SignalGenerator):
    """リアルタイム価格を供給し、TSLの評価と約定監視を行う。"""
    symbols = cfg.get_sync("trading.symbols", ["USD_JPY"])

    async def stream_for_pair(pair):
        while True:
            try:
                async for price_data in pricing_stream_full(pair):
                    bid = price_data["bid"]
                    ask = price_data["ask"]
                    mid = (bid + ask) / 2.0
                    
                    # 【新規追加】 1. リアルタイムTSL評価（本番用最強の矛）
                    open_trades = [p for p in broker.positions if p["pair"] == pair]
                    for trade in open_trades:
                        await signal_gen._check_and_execute_tsl(trade, mid)
                        
                    # 2. ブローカー側の決済判定・Kill Switch等（最強の盾）
                    await broker.update_market_price(pair, bid, ask)
                    
            except Exception as e:
                await log_error(logger, f"virtual_stream_{pair}", e)
                await asyncio.sleep(5)

    tasks = [stream_for_pair(pair) for pair in symbols]
    await asyncio.gather(*tasks)
# ＝＝＝ ▲ 修正箇所2 ▲ ＝＝＝

# 2. run_virtual_forward_test 関数全体を上書き
async def run_virtual_forward_test():
    logger.info("Starting Virtual Forward Test Mode...")

    broker = VirtualBroker(initial_balance=1000000.0)
    guard = GuardCenter()
    executor = VirtualExecutorAdapter(broker)
    signal_gen = SignalGenerator(executor=executor)

    symbols = cfg.get_sync("trading.symbols", ["USD_JPY"])
    loop_interval = cfg.get_sync("system.loop_interval_sec", 60)

    price_stream_task = asyncio.create_task(virtual_price_stream_task(broker, signal_gen))
    
    # ▼ 追加: ニュース更新ループタスクの起動 ▼
    from core.monitor import news_update_loop
    news_task = asyncio.create_task(news_update_loop())
    
    logger.info("Virtual loop started.", symbols=symbols, interval=loop_interval)

    try:
        while True:
            cfg.load_config()
            trade_tasks = [virtual_trade_cycle(pair, signal_gen, broker, guard) for pair in symbols]
            await asyncio.gather(*trade_tasks)
            await asyncio.sleep(loop_interval)
    except asyncio.CancelledError:
        logger.info("Virtual trading loop cancelled.")
    finally:
        price_stream_task.cancel()
        news_task.cancel() # 追加: 終了時にタスクをキャンセル
        await asyncio.gather(price_stream_task, news_task, return_exceptions=True)
        logger.info("All background tasks cancelled.")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        asyncio.run(run_virtual_forward_test())
    except KeyboardInterrupt:
        logger.info("Virtual mode shutdown.")