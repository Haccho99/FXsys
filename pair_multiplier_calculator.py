"""
pair_multiplier_calculator.py - WFA結果から各通貨ペアのPFを集計し、動的資金配分倍率をconfig.jsonに反映する
"""
import sys
import json
import glob
from pathlib import Path
from datetime import datetime, timezone, timedelta
import polars as pl
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from core.config_manager import ConfigManager
from core.logger import get_logger

cfg = ConfigManager(PROJECT_ROOT)
logger = get_logger(cfg, "multiplier_calc")

def get_latest_wfa_report(log_dir: Path) -> Path | None:
    """最新のWFA最終レポートファイルを取得する"""
    pattern = str(log_dir / "wfa_final_report_*.parquet")
    files = sorted(glob.glob(pattern))
    if not files:
        # アーカイブフォルダーも念のため探索
        archive_pattern = str(log_dir / "**" / "wfa_final_report_*.parquet")
        files = sorted(glob.glob(archive_pattern, recursive=True))
    return Path(files[-1]) if files else None

def calculate_multipliers_from_df(df: pl.DataFrame, target_pairs: list[str], days: int = 90) -> dict[str, float]:
    """過去指定日数分のWFAデータから各ペアのPFを集計し、0.5〜1.5の倍率を算出する"""
    if "Pair" not in df.columns and "pair" in df.columns:
        df = df.rename({"pair": "Pair"})

    # 日付フィルタリング (直近N日)
    if "Exit Timestamp" in df.columns:
        max_time = df["Exit Timestamp"].max()
        if max_time is not None:
            start_time = max_time - timedelta(days=days)
            df = df.filter(pl.col("Exit Timestamp") >= start_time)
            logger.info(f"Filtered WFA records from {start_time} to {max_time} (Past {days} days)")

    # ペアごとのPF集計
    pair_pfs = {}
    for pair in target_pairs:
        pair_df = df.filter(pl.col("Pair") == pair)
        total_trades = len(pair_df)
        
        if total_trades < 5:
            logger.warning(f"[{pair}] Insufficient trades ({total_trades}). Defaulting PF to 1.0.")
            pair_pfs[pair] = 1.0
            continue

        wins = pair_df.filter(pl.col("PnL") > 0)
        losses = pair_df.filter(pl.col("PnL") <= 0)
        
        total_profit = float(wins["PnL"].sum() or 0.0)
        total_loss = abs(float(losses["PnL"].sum() or 0.0))

        if total_loss == 0.0:
            pf = 3.0 if total_profit > 0 else 1.0
        else:
            pf = total_profit / total_loss
        
        # 0.0〜3.0 の範囲で安全にクリップ
        pair_pfs[pair] = max(0.0, min(pf, 3.0))

    logger.info(f"Calculated 3-Month WFA Profit Factors: {pair_pfs}")

    # 成績（PF）の良い順にソート
    sorted_pairs = sorted(pair_pfs.keys(), key=lambda x: pair_pfs[x], reverse=True)
    
    # ユーザー指定のランクベース固定倍率 (7ペアの場合を想定、ペア数が異なる場合は線形補間などで対応)
    # デフォルトの7ペアの場合は [1.50, 1.25, 1.00, 1.00, 1.00, 0.75, 0.50] を割り当てる
    desired_mults = [1.50, 1.25, 1.00, 1.00, 1.00, 0.75, 0.50]
    
    multipliers = {}
    if len(sorted_pairs) == 7:
        for i, pair in enumerate(sorted_pairs):
            multipliers[pair] = desired_mults[i]
    else:
        # ペア数が7以外の場合は、0.5〜1.5の間で均等に割り振る（合計は要素数と同一）
        n = len(sorted_pairs)
        if n == 0:
            return {}
        elif n == 1:
            multipliers[sorted_pairs[0]] = 1.0
        else:
            # np.linspaceを使って均等な倍率を作成
            mults = np.linspace(1.5, 0.5, n)
            # 合計がnになるように微調整（必要に応じて）
            # sum(np.linspace(1.5, 0.5, n)) は常に n になるため正規化不要
            for i, pair in enumerate(sorted_pairs):
                multipliers[pair] = round(float(mults[i]), 2)
                
    # 投資総額の確認 (安全のための最終チェック)
    total_mult = sum(multipliers.values())
    logger.info(f"Assigned rank-based multipliers: {multipliers} (Total: {total_mult:.2f})")

    return multipliers

def update_config_multipliers(new_multipliers: dict[str, float], config_path: Path) -> bool:
    """config.json の dynamic_allocation.pair_multipliers をアトミックに安全更新する"""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)

        if "dynamic_allocation" not in config_data:
            config_data["dynamic_allocation"] = {
                "enabled": True,
                "evaluation_metric": "PF",
                "multiplier_min": 0.5,
                "multiplier_max": 1.5,
                "pair_multipliers": {}
            }

        old_multipliers = config_data["dynamic_allocation"].get("pair_multipliers", {})
        config_data["dynamic_allocation"]["pair_multipliers"] = new_multipliers

        # アトミック書き込み (一時ファイル作成 -> リネーム置換)
        temp_file = config_path.with_suffix(".json.tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)
        temp_file.replace(config_path)

        logger.info("=== Dynamic Allocation Multipliers Successfully Updated in config.json ===")
        for pair, new_val in new_multipliers.items():
            old_val = old_multipliers.get(pair, 1.0)
            logger.info(f"  - {pair:7s}: {old_val:.2f}x -> {new_val:.2f}x")

        return True
    except Exception as e:
        logger.error(f"Failed to update config.json: {e}", exc_info=True)
        return False

def update_pair_multipliers_from_wfa(months: int = 3) -> bool:
    """WFA完了後に呼び出されるメインエントリポイント"""
    log_dir = PROJECT_ROOT / cfg.get_sync("system.log_dir", "logs")
    config_path = PROJECT_ROOT / "config.json"
    
    target_pairs = cfg.get_sync("trading.symbols", [
        "USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY", "AUD_USD", "EUR_USD", "GBP_USD"
    ])

    report_path = get_latest_wfa_report(log_dir)
    if not report_path or not report_path.exists():
        logger.warning("No WFA final report found. Cannot update pair multipliers.")
        return False

    logger.info(f"Reading WFA report from: {report_path.name}")
    df = pl.read_parquet(report_path)
    
    new_multipliers = calculate_multipliers_from_df(df, target_pairs, days=months * 30)
    return update_config_multipliers(new_multipliers, config_path)

if __name__ == "__main__":
    update_pair_multipliers_from_wfa(months=3)
