"""
scheduler.py - システム全体のジョブスケジューラ (vFinal - WFA, Multiplier, Discord & Status Integration)
"""
from __future__ import annotations
import sys
import os
import asyncio
import subprocess
from pathlib import Path
import schedule
import time
from datetime import datetime, timezone, timedelta
import json

project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from core.config_manager import ConfigManager
cfg = ConfigManager(project_root)
from core.logger import get_logger
from core.status_manager import set_pipeline_status, get_pipeline_status
from core.notifier import send_discord_message, send_discord_message_async

logger = get_logger(cfg, "scheduler")

async def run_job_async(script_name: str, args: list = None) -> bool:
    """
    非同期で別プロセスとしてスクリプトを実行する。
    WFAのような重い処理でも、スケジューラ自体をフリーズさせない。
    """
    if args is None:
        args = []
    
    script_path = project_root / script_name
    if not script_path.exists():
        logger.warning(f"Script '{script_name}' does not exist at {script_path}. Skipping.")
        return True # ファイルが存在しない場合はパイプライン全体を止めずにスキップ
    
    logger.info(f"Starting job: {script_name} with args {args}")
    
    try:
        command = f'"{sys.executable}" "{script_path}" ' + " ".join(args)
        
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(project_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            logger.info(f"Job '{script_name}' completed successfully.\n{stdout.decode('utf-8', errors='ignore')[:1000]}")
            return True
        else:
            logger.error(f"Job '{script_name}' failed (Code {process.returncode}).\n{stderr.decode('utf-8', errors='ignore')}")
            return False
            
    except Exception as e:
        logger.exception(f"An unexpected error occurred while running job '{script_name}': {e}")
        return False

# ＝＝＝ ▼ 週次4段階連鎖パイプライン ▼ ＝＝＝
async def run_weekly_pipeline():
    """
    設定された日時に実行される、システムの自己進化＆検証パイプライン
    """
    start_time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logger.info("=== Starting Weekly Automation Pipeline ===")
    
    # 状態を 'running' に更新
    await set_pipeline_status(
        status="running",
        phase="第1段階: Walk-Forward Analysis 実行中...",
        progress=0.10,
        details={"start_time": start_time_str}
    )
    
    # Discord開始通知
    await send_discord_message_async(
        f"🚀 **【週次自動パイプライン開始】**\n"
        f"> 実行開始: `{start_time_str}`\n"
        f"> 内容: WFA最適化、ロット傾斜配分自動更新、検証バックテストを順次実行します。"
    )
    
    debug_log_path = project_root / "logs" / "wfa_manual_debug.log"
    def _write_debug(msg):
        try:
            with open(debug_log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        except:
            pass

    _write_debug("=== Starting Weekly Automation Pipeline ===")
    
    try:
        # 第1段階: WFA実行 (最適化とバックテストによる実績データの生成)
        logger.info("Phase 1: Running Walk-Forward Analysis...")
        _write_debug("Phase 1: Starting run_wfa.py...")
        await set_pipeline_status(status="running", phase="第1段階: Walk-Forward Analysis (run_wfa.py) 実行中...", progress=0.20)
        success_p1 = await run_job_async("run_wfa.py")
        if not success_p1:
            logger.warning("Phase 1 (run_wfa.py) finished with warning/error.")
            _write_debug("Phase 1: WARNING/ERROR during run_wfa.py. Please check system_*.log for details.")
        else:
            _write_debug("Phase 1: run_wfa.py completed successfully.")

        # 第2段階: 資金傾斜配分更新 (WFA結果からPFを算出し、config.jsonを更新)
        logger.info("Phase 2: Updating Pair Multipliers...")
        _write_debug("Phase 2: Starting pair_multiplier_calculator.py...")
        await set_pipeline_status(status="running", phase="第2段階: ロット傾斜配分自動計算 (pair_multiplier_calculator.py)...", progress=0.50)
        success_p2 = await run_job_async("pair_multiplier_calculator.py")
        _write_debug(f"Phase 2: pair_multiplier_calculator.py completed. Success={success_p2}")

        # 第3段階: 最新設定での一括検証バックテスト実行
        logger.info("Phase 3: Running Weekly Verification Backtest...")
        _write_debug("Phase 3: Starting replay_backtester.py...")
        await set_pipeline_status(status="running", phase="第3段階: 週次検証バックテスト実行中...", progress=0.75)
        if (project_root / "replay_backtester.py").exists():
            # 1年間のフルバックテストを回避し、直近30日の最新環境への適応度をテストする
            end_dt = datetime.now()
            start_dt = end_dt - timedelta(days=30)
            start_str = start_dt.strftime("%Y-%m-%dT00:00:00")
            end_str = end_dt.strftime("%Y-%m-%dT23:59:59")
            _write_debug(f"Phase 3: Running with args --start {start_str} --end {end_str}")
            success_p3 = await run_job_async("replay_backtester.py", ["--start", start_str, "--end", end_str])
            _write_debug(f"Phase 3: replay_backtester.py completed. Success={success_p3}")

        # 第4段階: 結果通知とAI分析官による自己修正
        logger.info("Phase 4: Generating WFA Analysis Report...")
        _write_debug("Phase 4: Starting analyze_comprehensive.py...")
        await set_pipeline_status(status="running", phase="第4段階: 分析レポート生成中...", progress=0.90)
        
        analyzer_script = project_root / "analyze_comprehensive.py"
        wfa_analysis_out = project_root / "data" / "wfa_latest_analysis.txt"
        
        if analyzer_script.exists():
            try:
                cmd = f'"{sys.executable}" "{analyzer_script}" --csv data/reports/decision_log_multi_pairs.csv > "{wfa_analysis_out}"'
                process = await asyncio.create_subprocess_shell(
                    cmd,
                    cwd=str(project_root)
                )
                await process.communicate()
                if process.returncode == 0:
                    logger.info("Phase 4 (Analysis Report Generation) completed successfully.")
                    _write_debug("Phase 4: Analysis Report Generation completed successfully.")
                else:
                    logger.error(f"Phase 4 failed with return code {process.returncode}")
                    _write_debug(f"Phase 4: ERROR - return code {process.returncode}")
            except Exception as e:
                logger.error(f"Phase 4 exception: {e}")
                _write_debug(f"Phase 4: EXCEPTION - {e}")

        # 最新の傾斜倍率を取得
        cfg.config = cfg.load_config()
        dyn_cfg = cfg.get_sync("dynamic_allocation", {})
        pair_mults = dyn_cfg.get("pair_multipliers", {})
        mult_str = "\n".join([f"• **{p}**: `{m:.2f}倍`" for p, m in pair_mults.items()])
        _write_debug(f"Pipeline finished. Final Multipliers:\n{mult_str}")

        # 完了状態の保存
        finish_time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        await set_pipeline_status(
            status="completed",
            phase="週次自動パイプライン 正常完了",
            progress=1.0,
            details={
                "start_time": start_time_str,
                "finish_time": finish_time_str,
                "pair_multipliers": pair_mults
            }
        )
        
        # Discord完了通知
        await send_discord_message_async(
            f"✅ **【週次自動パイプライン完了】**\n"
            f"> 完了時刻: `{finish_time_str}`\n\n"
            f"📊 **最新のロット傾斜配分 (Pair Multipliers)**:\n{mult_str}\n\n"
            f"※新設定は次週の取引より自動適用されます。\n"
            f"👉 **アクション要請:** ダッシュボードの「AI分析＆レポート」タブから最新のWFAバックテスト結果を確認し、必要に応じて『AIに分析させる』ボタンを実行してください。"
        )
        logger.info("=== Weekly Automation Pipeline Successfully Completed ===")

    except Exception as e:
        logger.exception(f"Critical error during weekly pipeline execution: {e}")
        await set_pipeline_status(
            status="failed",
            phase=f"エラー発生: {str(e)[:100]}",
            progress=0.0,
            details={"error": str(e)}
        )
        await send_discord_message_async(
            f"🚨 **【週次パイプライン実行エラー】**\n"
            f"> パイプライン実行中にエラーが発生しました:\n```{str(e)[:300]}```\n"
            f"> ログファイルをご確認ください。"
        )

def register_all_jobs():
    """
    config.jsonの設定に基づいて全スケジュールジョブをクリア＆再登録する
    """
    schedule.clear()
    logger.info("--- Registering Scheduled Jobs from config.json ---")

    # 1. ログクリーナー (毎日 03:00)
    schedule.every().day.at("03:00").do(
        lambda: asyncio.create_task(run_job_async("log_cleaner.py"))
    )
    logger.info("Scheduled: 'log_cleaner.py' -> Daily at 03:00")

    # 2. WFA & 週次4段階自動化パイプライン
    wfa_task = cfg.get_sync("tasks.walk_forward_analyzer", {})
    if wfa_task.get("enabled", True):
        raw_wfa_sched = wfa_task.get("schedule") or wfa_task.get("cron_schedule") or "15 11 * * 6"
        parts = raw_wfa_sched.split()
        m_val = parts[0].zfill(2) if len(parts) >= 1 and parts[0].isdigit() else "15"
        h_val = parts[1].zfill(2) if len(parts) >= 2 and parts[1].isdigit() else "11"
        dow_val = parts[4] if len(parts) >= 5 else "6"
        parsed_wfa_time = f"{h_val}:{m_val}"

        dow_map = {
            "0": (schedule.every().sunday, "Sunday"),
            "1": (schedule.every().monday, "Monday"),
            "2": (schedule.every().tuesday, "Tuesday"),
            "3": (schedule.every().wednesday, "Wednesday"),
            "4": (schedule.every().thursday, "Thursday"),
            "5": (schedule.every().friday, "Friday"),
            "6": (schedule.every().saturday, "Saturday"),
            "*": (schedule.every().day, "Daily"),
        }
        job_builder, dow_name = dow_map.get(dow_val, (schedule.every().saturday, "Saturday"))
        job_builder.at(parsed_wfa_time).do(
            lambda: asyncio.create_task(run_weekly_pipeline())
        )
        logger.info(f"Scheduled: 'Weekly Pipeline' -> Weekly on {dow_name} at {parsed_wfa_time}")

    # 3. その他 config.json タスク
    tasks = cfg.get_sync("tasks", {})
    for task_name, task_config in tasks.items():
        try:
            if task_name in ["walk_forward_analyzer", "weekly_pipeline"]:
                continue

            if not task_config.get("enabled", False): 
                continue
                
            raw_schedule = task_config.get("schedule_time") or task_config.get("schedule") or task_config.get("schedule_summer")
            if not raw_schedule:
                continue

            parsed_time = raw_schedule
            if ":" not in raw_schedule:
                parts = raw_schedule.split()
                if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                    parsed_time = f"{parts[1].zfill(2)}:{parts[0].zfill(2)}"
                else:
                    continue

            if task_name == "short_term_optimizer":
                schedule.every().day.at(parsed_time).do(
                    lambda: asyncio.create_task(run_job_async("core/backtest/optimize.py", ["--mode", "short_term"]))
                )
                logger.info(f"Scheduled: '{task_name}' -> Daily at {parsed_time}")
            elif task_name == "data_downloader":
                schedule.every().day.at(parsed_time).do(
                    lambda: asyncio.create_task(run_job_async("data_downloader.py"))
                )
                logger.info(f"Scheduled: '{task_name}' -> Daily at {parsed_time}")
            else:
                script_file = f"{task_name}.py"
                schedule.every().day.at(parsed_time).do(
                    lambda s=script_file: asyncio.create_task(run_job_async(s))
                )
                logger.info(f"Scheduled: '{task_name}' ({script_file}) -> Daily at {parsed_time}")
                
        except Exception as e:
            logger.error(f"Failed to schedule task '{task_name}': {e}")

    logger.info(f"Scheduler initialized: {len(schedule.get_jobs())} active background jobs registered.")

async def main():
    logger.info("=== System Scheduler Starting ===")
    config_file_path = project_root / "config.json"
    last_mtime = config_file_path.stat().st_mtime if config_file_path.exists() else 0
    
    register_all_jobs()
    
    check_mtime_counter = 0
    while True:
        schedule.run_pending()
        
        # 5秒ごとに config.json の更新（Hot Reload）を監視
        check_mtime_counter += 1
        if check_mtime_counter >= 5:
            check_mtime_counter = 0
            if config_file_path.exists():
                current_mtime = config_file_path.stat().st_mtime
                if current_mtime > last_mtime:
                    logger.info("⚡ Detected changes in config.json! Reloading scheduler jobs dynamically (Hot Reload)...")
                    last_mtime = current_mtime
                    cfg.config = cfg.load_config()
                    register_all_jobs()

        await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user.")
    except Exception as e:
        logger.critical("Scheduler failed critically.", exc_info=True)