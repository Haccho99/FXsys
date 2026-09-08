"""
main_loop.py (v17 - Corrected Imports & Controller Logic)
"""
import asyncio
import schedule
import sys
from pathlib import Path

from core import cfg
from core.logger import get_logger, log_error
from core.data import fetch_candles, pricing_stream, get_spread # ★★★ get_spreadを正しくdataからインポート ★★★
from core.monitor import GuardCenter
from core.signal_module import SignalGenerator
from core.trade import get_executor, TradeExecutor
from core.redis_client import create_redis_client
from core.system_controller import SystemController

logger = get_logger(cfg, "main_loop")

async def run_optimization_job():
    """短期パラメータ最適化ジョブをサブプロセスとして実行する。"""
    logger.info("Starting scheduled short-term optimization job...")
    command = f"{sys.executable} optimize.py --mode short_term"
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode == 0:
            logger.info("Short-term optimization job completed successfully.", stdout=stdout.decode())
        else:
            logger.error("Short-term optimization job failed.", stderr=stderr.decode())
    except Exception as e:
        await log_error(logger, "run_optimization_job", e)

async def run_archiver_job():
    """データアーカイブジョブをサブプロセスとして実行する。"""
    logger.info("Starting scheduled data archiver job...")
    command = f"{sys.executable} data_archiver.py"
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode == 0:
            logger.info("Data archiver job completed successfully.", stdout=stdout.decode())
        else:
            logger.error("Data archiver job failed.", stderr=stderr.decode())
    except Exception as e:
        await log_error(logger, "run_archiver_job", e)

async def run_scheduled_tasks():
    """スケジュールされたタスク（アーカイブ、最適化など）を管理・実行する。"""
    logger.info("Initializing scheduler...")
    
    if cfg.get_sync("tasks.archiver.enabled", False):
        schedule.every().day.at(cfg.get_sync("tasks.archiver.schedule_time", "04:00")).do(lambda: asyncio.create_task(run_archiver_job()))
        logger.info(f"Scheduled data archiver job daily at {cfg.get_sync('tasks.archiver.schedule_time')}.")

    if cfg.get_sync("tasks.short_term_optimizer.enabled", False):
        schedule.every().day.at(cfg.get_sync("tasks.short_term_optimizer.schedule_time", "06:00")).do(lambda: asyncio.create_task(run_optimization_job()))
        logger.info(f"Scheduled short-term optimization job daily at {cfg.get_sync('tasks.short_term_optimizer.schedule_time')}.")

    if not schedule.jobs:
        logger.warning("No jobs scheduled. Scheduler will not run.")
        return

    while True:
        await asyncio.to_thread(schedule.run_pending)
        await asyncio.sleep(60)

async def price_stream_tick_task(signal_gen: SignalGenerator):
    """全ての取引可能ペアの価格を監視し、on_tickを呼び出すタスク。"""
    symbols = cfg.get_sync("trading.symbols", [])
    use_simulation = cfg.get_sync("system.use_price_simulation", False)
    
    async def stream_for_pair(pair):
        while True: # 常に再接続を試みるための無限ループ
            try:
                logger.info(f"Connecting to price stream for {pair}...")
                async for price in pricing_stream(pair, force_simulation=use_simulation):
                    await signal_gen.on_tick(pair, mid_price=price)
            except Exception as e:
                # 接続が切れた場合、エラーを記録して5秒待機後に再試行
                await log_error(logger, f"price_stream_for_{pair}", e)
                logger.warning(f"Price stream for {pair} disconnected. Reconnecting in 5 seconds...")
                await asyncio.sleep(5)

    stream_tasks = [stream_for_pair(pair) for pair in symbols]
    await asyncio.gather(*stream_tasks)
# ▲▲▲ 修正ここまで ▲▲▲

async def trade_cycle_for_pair(pair: str, signal_gen: SignalGenerator, executor: TradeExecutor, controller: SystemController):
    """単一の通貨ペアに対する取引サイクルを実行する。"""
    try:
        current_status = await controller.get_trading_status()
        if current_status == "PAUSED":
            logger.info(f"Trading is currently PAUSED. Skipping signal generation for {pair}.")
            return

        logger.debug(f"Fetching data for {pair}...")
        df_m15 = await fetch_candles(pair, gran="M15", count=1500)
        if df_m15.is_empty() or len(df_m15) < 50:
            logger.warning(f"Not enough data for {pair} to generate signal.")
            return

        spread_pips = await get_spread(pair)
        
        logger.debug(f"Generating signal for {pair}...")
        signal = await signal_gen.generate_signal(
            pair=pair, df_m15=df_m15, spread_pips=spread_pips
        )

        if signal:
            logger.warning("Signal generated.", signal=signal)
            await executor.open_position(signal)
            
    except Exception as e:
        await log_error(logger, "trade_cycle_for_pair", e, pair=pair)

async def run_trading_loop():
    logger.info("Initializing trading loop components...")
    
    executor = await get_executor()
    signal_gen = SignalGenerator(executor=executor)
    
    # ▼▼▼【修正】controllerをここで一度だけ初期化 ▼▼▼
    redis_client = create_redis_client(cfg)
    if not redis_client:
        logger.critical("Failed to create Redis client for SystemController. Shutting down.")
        return
    controller = SystemController(cfg, redis_client)
    
    symbols = cfg.get_sync("trading.symbols", [])
    loop_interval = cfg.get_sync("system.loop_interval_sec", 60)
    
    scheduler_task = asyncio.create_task(run_scheduled_tasks())
    price_tick_task = asyncio.create_task(price_stream_tick_task(signal_gen))

    logger.info("Trading loop initialized. Starting main cycle.", symbols=symbols, interval=loop_interval)
    
    try:
        while True:
            cfg.load_config()
            
            # ▼▼▼【修正】trade_cycle_for_pairの呼び出し側を定義と一致させる ▼▼▼
            trade_tasks = [trade_cycle_for_pair(pair, signal_gen, executor, controller) for pair in symbols]
            await asyncio.gather(*trade_tasks)
            
            logger.debug(f"Main trading cycle finished. Waiting for {loop_interval} seconds.")
            await asyncio.sleep(loop_interval)
            
    except asyncio.CancelledError:
        logger.info("Trading loop cancelled.")
    finally:
        scheduler_task.cancel()
        price_tick_task.cancel()
        await asyncio.gather(scheduler_task, price_tick_task, return_exceptions=True)
        logger.info("All background tasks cancelled. Trading loop has shut down.")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(run_trading_loop())
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user.")