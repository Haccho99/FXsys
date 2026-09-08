# core/backtest/optimize.py
import sys, argparse, asyncio, json, uuid, os
from pathlib import Path
from functools import partial
import concurrent.futures
import pandas as pd
import optuna
from typing import Optional, Dict, Any

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path: sys.path.append(str(project_root))

from core.config_manager import ConfigManager
cfg = ConfigManager(project_root)
from core.logger import get_logger, log_error
from core.backtest.backtester import run_backtest
from core.notify import notify_all
# from core.discord_ui import ParamProposalView
from core.redis_client import create_redis_client
from core.system_controller import SystemController

logger = get_logger(cfg, "optimizer")

def suggest_params(trial: optuna.Trial, param_space: dict):
    params = {}
    for param_name, space in param_space.items():
        if space["type"] == "int": 
            params[param_name] = trial.suggest_int(param_name, space["low"], space["high"])
        elif space["type"] == "float": 
            params[param_name] = trial.suggest_float(param_name, space["low"], space["high"])
        elif space["type"] == "categorical": 
            # 曜日フィルターなどの真偽値（True/False）や文字列リストの探索に対応
            params[param_name] = trial.suggest_categorical(param_name, space["choices"])
    return params

async def objective_async(trial: optuna.Trial, pair: str, strategy: str, param_space: dict, candle_count: int, start_date: Optional[str], end_date: Optional[str]):
    try:
        params = suggest_params(trial, param_space)
        # Add PID to filename to prevent race conditions in parallel execution
        output_filename = f"optimize_backtest_log_pid_{os.getpid()}_trial_{trial.number}.parquet"
        backtest_kwargs = { "pair": pair, "strategy_name": strategy, "params": params, "output_log_filename": output_filename}
        if start_date and end_date:
            backtest_kwargs["start_date_str"] = start_date
            backtest_kwargs["end_date_str"] = end_date
        else:
            backtest_kwargs["candle_count"] = candle_count
        
        result = await run_backtest(**backtest_kwargs)

        # 1. 【重大な修正】Noneチェックを最初に実行し、クラッシュ（AttributeError）を防ぐ
        if result is None:
            logger.warning(f"Trial {trial.number}: Backtest failed (result is None). Pruning.")
            raise optuna.exceptions.TrialPruned()

        min_trades = 30  # 最低取引回数を30回に引き上げ（過学習防止）
        total_trades = result.get("Total Trades", 0)
        
        if total_trades < min_trades:
            logger.warning(f"Trial {trial.number}: Fewer than {min_trades} trades ({total_trades} trades). Pruning.")
            raise optuna.exceptions.TrialPruned()

        # 各種指標の取得
        sharpe_ratio = result.get("Sharpe Ratio", -1.0)
        win_rate = result.get("Win Rate [%]", 0.0) / 100.0
        profit_factor = result.get("Profit Factor", 0.0)

        import numpy as np
        # 2. 【重大な修正】NaN および 無限大(inf) のチェックと安全処理
        if pd.isna(sharpe_ratio) or pd.isna(profit_factor):
            logger.warning(f"Trial {trial.number}: Sharpe Ratio or Profit Factor was NaN. Pruning.")
            raise optuna.exceptions.TrialPruned()
            
        if np.isinf(profit_factor):
            # 損失0でinfになった場合は、AIの異常な優遇を防ぐため現実的な上限値でキャップする
            profit_factor = 10.0

        # 【評価関数の高度化】
        # PFを主軸とし、勝率とSharpe Ratioで補正。
        weight_pf = 0.6
        weight_sharpe = 0.2
        weight_win_rate = 0.2
        
        # 取引回数が多いことに対する緩やかなボーナス（上限あり）
        trade_bonus_multiplier = min(1.2, 1.0 + ((total_trades - min_trades) / 150.0))
        
        # 総合スコアの算出
        base_score = (profit_factor * weight_pf) + (sharpe_ratio * weight_sharpe) + (win_rate * weight_win_rate)
        comprehensive_score = base_score * trade_bonus_multiplier

        logger.info(f"Trial {trial.number}: Score={comprehensive_score:.4f} (PF={profit_factor:.2f}, Sharpe={sharpe_ratio:.2f}, WinRate={win_rate:.2%}), Trades={total_trades}")
        
        return comprehensive_score

    except optuna.exceptions.TrialPruned as e:
        logger.info(f"Trial {trial.number} pruned: {e}")
        raise
    except Exception as e:
        await log_error(logger, f"objective_async_trial_{trial.number}", e)
        return -2.0

def objective_sync_wrapper(trial: optuna.Trial, main_loop: asyncio.AbstractEventLoop, async_func: callable):
    future = asyncio.run_coroutine_threadsafe(async_func(trial), main_loop)
    return future.result()

async def run_optimization_for_strategy(pair: str, strategy: str, param_space: dict, n_trials: int, candle_count: int, executor: concurrent.futures.Executor, start_date: Optional[str], end_date: Optional[str]):
    main_loop = asyncio.get_running_loop()
    logger.info(f"--- Starting optimization for Pair: {pair}, Strategy: {strategy} ---")
    study = optuna.create_study(direction="maximize")
    objective_with_args = partial(objective_async, pair=pair, strategy=strategy, param_space=param_space, candle_count=candle_count, start_date=start_date, end_date=end_date)
    sync_objective = partial(objective_sync_wrapper, main_loop=main_loop, async_func=objective_with_args)
    optimize_func_with_trials = partial(study.optimize, sync_objective, n_trials=n_trials, catch=(Exception,))
    await main_loop.run_in_executor(executor, optimize_func_with_trials)
    logger.info(f"--- Finished optimization for Pair: {pair}, Strategy: {strategy} ---")
    return pair, strategy, study

async def run_full_optimization(
    pair: str, strategy_name: str, start_date: Optional[str], end_date: Optional[str], 
    output_suffix: Optional[str], walk_number: Optional[int] = None, 
    train_start: Optional[str] = None, train_end: Optional[str] = None, 
    test_start: Optional[str] = None, test_end: Optional[str] = None, 
    wfa_mode: bool = False
):
    logger.info(f"--- Running Optimization for {pair}/{strategy_name} ---")
    optimizer_config = cfg.get_sync("tasks.optimizer", {})
    n_trials = optimizer_config.get("n_trials", 50)
    param_spaces = optimizer_config.get("parameter_space", {})
    candle_count = optimizer_config.get("backtest_candle_count", 2880)
    data_dir = Path(cfg.get_sync("system.data_dir", "data"))
    data_dir.mkdir(exist_ok=True)

    if strategy_name not in param_spaces:
        logger.critical(f"Parameter space for strategy '{strategy_name}' not defined. Halting.")
        sys.exit(1)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        task = run_optimization_for_strategy(
            pair, strategy_name, param_spaces[strategy_name], n_trials, 
            candle_count, executor, start_date, end_date
        )
        results = await asyncio.gather(task)
        pair, strategy_name, study = results[0]

    # ＝＝＝ ▼ 修正: Optuna全トライアル失敗時の安全装置（Graceful Shutdown） ▼ ＝＝＝
    try:
        best_trial = study.best_trial
    except ValueError:
        logger.warning(
            f"[{pair}/{strategy_name}] Optuna optimization finished with NO completed trials (all pruned or failed). "
            "Market conditions may be too strict. Skipping this optimization task safely."
        )
        return  # システムをクラッシュさせず、この通貨ペア/戦略の最適化のみをスキップする

    if not best_trial:
        logger.error(f"[{pair}/{strategy_name}] Best trial not found after optimization.")
        return
    # ＝＝＝ ▲ 修正 ここまで ▲ ＝＝＝

    best_params = study.best_params

    # This notification logic is now less relevant in WFA mode but kept for standalone runs.
    if not wfa_mode:
        pass

    suffix = f"_{output_suffix}" if output_suffix else ""
    output_path = data_dir / f"best_params_{pair}{suffix}.json"
    
    # ＝＝＝ ▼ 修正: JSONの上書き防止（マージ保存処理） ▼ ＝＝＝
    import time
    import random
    
    # 並列処理時のファイルアクセスの衝突を防ぐため、リトライ付きで読み込み＆マージを行う
    max_retries = 5
    for attempt in range(max_retries):
        try:
            existing_data = {}
            # 既存のファイルがあれば読み込む
            if output_path.exists():
                with open(output_path, "r", encoding="utf-8") as f:
                    try:
                        existing_data = json.load(f)
                    except json.JSONDecodeError:
                        pass # ファイルが空、または他プロセスが書き込み中の場合は無視
            
            # 今回の戦略のパラメータを辞書に追加（マージ）
            existing_data[strategy_name] = best_params
            
            # 再度書き込み
            with open(output_path, "w", encoding="utf-8") as f: 
                json.dump(existing_data, f, indent=2)
                
            break # 成功したらループを抜ける
            
        except Exception as e:
            if attempt == max_retries - 1:
                logger.error(f"Failed to save params to {output_path} after {max_retries} attempts: {e}")
            else:
                # 衝突した場合はランダムに少し待ってからリトライ
                await asyncio.sleep(random.uniform(0.5, 1.5))
    # ＝＝＝ ▲ 修正 ここまで ▲ ＝＝＝

    logger.info(f"Best parameters for {pair}/{strategy_name} saved to {output_path}")

    final_log_filename = f"final_optimized_backtest_{pair}_{strategy_name}{suffix}.parquet"
    final_backtest_kwargs = {
        "pair": pair, "strategy_name": strategy_name, "params": best_params, 
        "output_log_filename": final_log_filename, "start_date_str": test_start,
        "end_date_str": test_end, "walk_number": walk_number, "train_start": train_start,
        "train_end": train_end, "test_start": test_start, "test_end": test_end
    }
    
    final_result = await run_backtest(**final_backtest_kwargs)
    if final_result is None:
        logger.warning(
            f"Final backtest for {pair}/{strategy_name} with best params resulted in an error or ZERO trades. "
            f"Intermediate file '{final_log_filename}' was NOT generated. Skipping this pair gracefully."
        )
        return
            
    logger.info(f"--- Optimization and final backtest for {pair}/{strategy_name} completed. ---")

async def run_short_term_optimization(start_date: Optional[str], end_date: Optional[str], output_suffix: Optional[str]):
    """短期データに基づき、日々のパラメータ最適化を実行する。"""
    logger.info("--- Starting Short-Term Optimization Script ---")
    
    # config.jsonから短期最適化用の設定を読み込む
    optimizer_config = cfg.get_sync("tasks.short_term_optimizer", {})
    if not optimizer_config:
        logger.critical("'tasks.short_term_optimizer' section not found in config.json. Exiting.")
        return

    pairs = optimizer_config.get("pairs_to_optimize", [])
    strategies = optimizer_config.get("strategies_to_optimize", [])
    n_trials = optimizer_config.get("n_trials", 20)
    # パラメータの探索範囲は、フルの最適化と共通のものを使用
    param_spaces = cfg.get_sync("tasks.optimizer.parameter_space", {})
    candle_count = optimizer_config.get("candle_count", 1008) # 約1週間分の15分足データ
    
    logger.info(f"Loaded configuration for short-term optimizer: {len(pairs)} pairs, {len(strategies)} strategies.")
    if not pairs or not strategies:
        logger.critical("No pairs or strategies are configured for short-term optimization. Exiting.")
        return

    data_dir = Path(cfg.get_sync("system.data_dir", "data"))
    data_dir.mkdir(exist_ok=True)

    all_best_params = {pair: {} for pair in pairs}

    # ThreadPoolExecutorを使い、各ペア・戦略の最適化を並列実行
    with concurrent.futures.ThreadPoolExecutor() as executor:
        tasks = []
        for pair in pairs:
            for strategy in strategies:
                if strategy in param_spaces:
                    logger.debug(f"Creating short-term optimization task for {pair}/{strategy}")
                    task = run_optimization_for_strategy(
                        pair=pair, 
                        strategy=strategy, 
                        param_space=param_spaces[strategy], 
                        n_trials=n_trials, 
                        candle_count=candle_count, 
                        executor=executor, 
                        start_date=start_date, 
                        end_date=end_date
                    )
                    tasks.append(task)
                else:
                    logger.warning(f"Parameter space for strategy '{strategy}' not defined. Skipping.")
        
        if not tasks:
            logger.critical("No short-term optimization tasks were created. Exiting.")
            return

        logger.info(f"Starting execution of {len(tasks)} short-term optimization tasks...")
        results = await asyncio.gather(*tasks)

    # 最適化結果を収集
    for pair, strategy, study in results:
        if study.best_trial:
            all_best_params[pair][strategy] = study.best_params
            logger.info(
                f"Short-term optimization for {pair}/{strategy} completed. Best Sharpe Ratio: {study.best_value:.4f}", 
                extra={"best_params": study.best_params}
            )
        else:
            logger.error(f"No successful trials for {pair}/{strategy} in short-term optimization.")

    # 最適化されたパラメータを best_params_short_term_{pair}.json ファイルに保存
    for pair, strategies_params in all_best_params.items():
        if not strategies_params: continue
        
        suffix = f"_{output_suffix}" if output_suffix else ""
        output_path = data_dir / f"best_params_short_term_{pair}{suffix}.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(strategies_params, f, indent=2, ensure_ascii=False)
        logger.info(f"All best short-term parameters for {pair} saved to {output_path}")

    logger.info("--- All short-term optimization tasks completed. ---")