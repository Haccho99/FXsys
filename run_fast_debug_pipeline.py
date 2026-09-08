import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

# 念のためプロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from core.config_manager import ConfigManager
from core.logger import get_logger

async def run_cmd(cmd: str, cwd: str):
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 実行中: {cmd}")
    process = await asyncio.create_subprocess_shell(
        cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    # リアルタイムで出力を表示しないと進捗がわからないが、長すぎるので省略気味に
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        print(f"エラー発生 (コード {process.returncode}):\n{stderr.decode('utf-8', errors='replace')}")
        return False
    print(f"成功: {cmd}")
    return True

async def main():
    print("=== 高速デバッグパイプライン検証開始 ===")
    cfg = ConfigManager(PROJECT_ROOT)
    
    # オリジナルの設定をバックアップ
    original_n_trials = cfg.get_sync("tasks.optimizer.n_trials", 50)
    
    try:
        # 1. 爆速で終わるように一時的に設定を書き換え (Trials=2)
        print("設定を一時的にデバッグ用(n_trials=2)に変更します...")
        cfg.config["tasks"]["optimizer"]["n_trials"] = 2
        cfg.save_config()

        # ターゲット期間を直近1週間に絞る (WFAを極力短くするため)
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=7)
        start_str = start_dt.strftime("%Y-%m-%dT00:00:00Z")
        end_str = end_dt.strftime("%Y-%m-%dT23:59:59Z")
        
        print("\n--- Phase 1: WFAパイプライン (1週間分のデータで実行) ---")
        # 直接 walk_forward_analyzer.py を呼び出すスクリプトを一時作成
        fast_wfa_script = PROJECT_ROOT / "fast_wfa_temp.py"
        with open(fast_wfa_script, "w", encoding="utf-8") as f:
            f.write(f"""import asyncio
from walk_forward_analyzer import WalkForwardAnalyzer
async def run():
    analyzer = WalkForwardAnalyzer(mode="manual", start_date="{start_str}", end_date="{end_str}")
    analyzer.run_analysis(export_profitable=False)
if __name__ == '__main__':
    asyncio.run(run())
""")
        
        success = await run_cmd(f"{sys.executable} fast_wfa_temp.py", str(PROJECT_ROOT))
        if fast_wfa_script.exists():
            fast_wfa_script.unlink()
            
        if not success:
            print("Phase 1 で失敗しました。")
            return

        print("\n--- Phase 2: ロット傾斜配分更新 (pair_multiplier_calculator.py) ---")
        success = await run_cmd(f"{sys.executable} pair_multiplier_calculator.py", str(PROJECT_ROOT))
        if not success:
            print("Phase 2 で失敗しました。")
            return
            
        # config.json の結果を確認
        cfg.load_config()
        multipliers = cfg.get_sync("dynamic_allocation.pair_multipliers", {})
        print(f"適用されたMultiplier: {multipliers}")

        print("\n--- Phase 3: 週次検証バックテスト (replay_backtester.py) ---")
        # 週末を避けるため、数日前の平日をテスト期間に設定
        p3_start = (end_dt - timedelta(days=4)).strftime("%Y-%m-%dT00:00:00")
        p3_end = (end_dt - timedelta(days=2)).strftime("%Y-%m-%dT23:59:59")
        success = await run_cmd(f"{sys.executable} replay_backtester.py --start {p3_start} --end {p3_end}", str(PROJECT_ROOT))
        if not success:
            print("Phase 3 で失敗しました。")
            return

        print("\n=== 全パイプラインの検証が正常に終了しました！ ===")

    finally:
        # 設定を元に戻す
        print(f"設定を元(n_trials={original_n_trials})に戻します...")
        cfg.config["tasks"]["optimizer"]["n_trials"] = original_n_trials
        cfg.save_config()

if __name__ == "__main__":
    asyncio.run(main())
