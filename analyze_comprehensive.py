# analyze_comprehensive.py
#!/usr/bin/env python3
"""
================================================================================
 📊 FXsys Comprehensive Performance & Risk Analyzer (analyze_comprehensive.py)
================================================================================
 このスクリプトは、FX自動取引システムのバックテストログ（virtual_trade_log_*.csv）
 または決定ログ（decision_log_*.csv）を読み込み、以下の多角的な要因別集計と
 リスク監査をワンストップで実行する公式統合分析ツールです。

 【主な分析機能】
 1. 全体総合パフォーマンス (Executive Summary: 勝率, PF, 純損益, 最大DD, RR比, EV)
 2. 防衛壁・ルール適合性監査 (Guard Audit: SL下限バリア遵守, 時間帯ガード, 週末ガード)
 3. 通貨ペア別パフォーマンス (Pair Breakdown)
 4. 戦略 (Strategy) & 売買方向 (Direction) 別パフォーマンス
 5. 決済理由別 (Exit Reason: TSL, SL, TP, Signal Exit) 詳細分析
 6. AIスコア階層別 (AI Score Tiers) 収益効率分析
 7. 曜日別 & JST時間帯別 (Day of Week & Hourly Time-window) パフォーマンス
 8. 連続損切り・連敗クラスタ分析 (Consecutive Loss Streaks & Drawdown Clustering)
 9. 月別推移 (Monthly Performance Breakdown)
 10. Markdown / JSON レポートの自動出力対応
================================================================================
"""

import sys
import os
import glob
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple

import pandas as pd
import numpy as np

# Windows環境でのUTF-8出力対応
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ==============================================================================
# 1. ヘルパー関数 & 指標計算エンジン
# ==============================================================================

def calc_pips(row: pd.Series) -> float:
    """通貨ペアに応じた損益pipsを算出する"""
    pair = str(row.get("pair", ""))
    is_jpy = "JPY" in pair
    pip_unit = 0.01 if is_jpy else 0.0001
    
    entry_p = float(row.get("entry_price", 0.0))
    exit_p = float(row.get("exit_price", 0.0))
    direction = str(row.get("direction", "long")).lower()
    
    if direction == "long" or direction == "buy":
        diff = exit_p - entry_p
    else:
        diff = entry_p - exit_p
        
    return diff / pip_unit if pip_unit > 0 else 0.0


def calculate_metrics(sub_df: pd.DataFrame, initial_balance: float = 1_000_000.0) -> Dict[str, Any]:
    """与えられたデータフレームから詳細なクオンツ・パフォーマンス指標を計算する"""
    n = len(sub_df)
    if n == 0:
        return {
            "trades": 0, "wins": 0, "losses": 0, "evens": 0,
            "win_rate": 0.0, "total_pnl": 0.0, "gross_profit": 0.0,
            "gross_loss": 0.0, "pf": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "rr_ratio": 0.0, "ev": 0.0, "mdd_amount": 0.0, "mdd_pct": 0.0,
            "avg_hold_min": 0.0, "med_hold_min": 0.0, "avg_pips": 0.0,
            "max_consecutive_wins": 0, "max_consecutive_losses": 0
        }

    pnl = sub_df["profit_amount"]
    wins = sub_df[pnl > 0]
    losses = sub_df[pnl < 0]
    evens = sub_df[pnl == 0]

    win_count = len(wins)
    loss_count = len(losses)
    even_count = len(evens)
    win_rate = win_count / n if n > 0 else 0.0

    total_pnl = pnl.sum()
    gross_profit = wins["profit_amount"].sum()
    gross_loss = abs(losses["profit_amount"].sum())

    if gross_loss > 0:
        pf = gross_profit / gross_loss
    elif gross_profit > 0:
        pf = float("inf")
    else:
        pf = 1.0

    avg_win = wins["profit_amount"].mean() if win_count > 0 else 0.0
    avg_loss = abs(losses["profit_amount"].mean()) if loss_count > 0 else 0.0
    rr_ratio = avg_win / avg_loss if avg_loss > 0 else (float("inf") if avg_win > 0 else 0.0)
    ev = total_pnl / n if n > 0 else 0.0

    # ドローダウン計算
    if "new_balance" in sub_df.columns and not sub_df["new_balance"].isna().all():
        bal_series = sub_df["new_balance"]
    else:
        bal_series = initial_balance + pnl.cumsum()

    peak = bal_series.cummax()
    dd_series = peak - bal_series
    mdd_amount = dd_series.max() if not dd_series.empty else 0.0
    
    # MDD率 (%)
    if peak.max() > 0:
        dd_pct_series = (dd_series / peak) * 100.0
        mdd_pct = dd_pct_series.max()
    else:
        mdd_pct = 0.0

    # 保有時間
    hold_min = sub_df["hold_time_min"] if "hold_time_min" in sub_df.columns else pd.Series([0.0]*n)
    avg_hold_min = hold_min.mean() if not hold_min.empty else 0.0
    med_hold_min = hold_min.median() if not hold_min.empty else 0.0

    # pips
    avg_pips = sub_df["pnl_pips"].mean() if "pnl_pips" in sub_df.columns else 0.0

    # 最大連勝数・最大連敗数
    max_c_wins, max_c_losses = 0, 0
    cur_c_wins, cur_c_losses = 0, 0
    for p in pnl:
        if p > 0:
            cur_c_wins += 1
            cur_c_losses = 0
            if cur_c_wins > max_c_wins: max_c_wins = cur_c_wins
        elif p < 0:
            cur_c_losses += 1
            cur_c_wins = 0
            if cur_c_losses > max_c_losses: max_c_losses = cur_c_losses
        else:
            cur_c_wins = 0
            cur_c_losses = 0

    return {
        "trades": n,
        "wins": win_count,
        "losses": loss_count,
        "evens": even_count,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "pf": pf,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "rr_ratio": rr_ratio,
        "ev": ev,
        "mdd_amount": mdd_amount,
        "mdd_pct": mdd_pct,
        "avg_hold_min": avg_hold_min,
        "med_hold_min": med_hold_min,
        "avg_pips": avg_pips,
        "max_consecutive_wins": max_c_wins,
        "max_consecutive_losses": max_c_losses
    }


# ==============================================================================
# 2. データローダー & クリーニング
# ==============================================================================

def load_dataset(
    target_path: Optional[str] = None,
    log_dir: str = "logs",
    pair_filter: Optional[str] = None,
    strategy_filter: Optional[str] = None
) -> pd.DataFrame:
    """指定されたCSVファイルまたはlogsディレクトリ配下のログを読み込み、クレンジングする"""
    df_list = []

    if target_path and Path(target_path).exists():
        path_obj = Path(target_path)
        if path_obj.is_file():
            print(f"📂 指定ファイルをロード中: {path_obj}")
            try:
                raw_df = pd.read_csv(path_obj)
                if not raw_df.empty: df_list.append(raw_df)
            except Exception as e:
                print(f"❌ ファイルの読み込みに失敗しました: {e}")
                return pd.DataFrame()
        elif path_obj.is_dir():
            files = sorted(path_obj.glob("virtual_trade_log_*.csv")) + sorted(path_obj.glob("*.csv"))
            for f in files:
                try:
                    raw_df = pd.read_csv(f)
                    if not raw_df.empty: df_list.append(raw_df)
                except Exception:
                    pass
    else:
        # デフォルト: logs/virtual_trade_log_*.csv を検索
        base_dir = Path(__file__).resolve().parent
        search_dir = base_dir / log_dir if not Path(log_dir).is_absolute() else Path(log_dir)
        files = sorted(search_dir.glob("virtual_trade_log_*.csv"))
        if not files:
            # 汎用CSV検索
            files = sorted(search_dir.glob("*.csv"))

        print(f"📂 ログディレクトリ ({search_dir}) より {len(files)} 件のログファイルを検出しました。")
        for f in files:
            try:
                raw_df = pd.read_csv(f)
                if not raw_df.empty:
                    df_list.append(raw_df)
            except Exception:
                pass

    if not df_list:
        print("⚠️ 有効な取引データが見つかりませんでした。")
        return pd.DataFrame()

    df = pd.concat(df_list, ignore_index=True)

    # カラム名マッピング（日本語カラム・表記ゆれ対応）
    rename_map = {
        "通貨ペア": "pair", "Pair": "pair",
        "売買": "direction", "売買区分": "direction", "Direction": "direction",
        "エントリー日時": "entry_time", "EntryTime": "entry_time", "Time": "entry_time",
        "決済日時": "timestamp", "ExitTime": "timestamp",
        "建値": "entry_price", "EntryPrice": "entry_price", "Price": "entry_price",
        "決済値": "exit_price", "ExitPrice": "exit_price",
        "損益額": "profit_amount", "損益": "profit_amount", "Profit": "profit_amount", "PnL": "profit_amount",
        "口座残高": "new_balance", "Balance": "new_balance",
        "決済理由": "reason", "ExitReason": "reason", "Reason": "reason",
        "戦略": "strategy", "Strategy": "strategy",
        "AIスコア": "ai_score", "AIScore": "ai_score", "Score": "ai_score",
        "数量": "lot_size", "ロット": "lot_size", "Units": "lot_size"
    }
    df = df.rename(columns=rename_map)

    # 必須カラムチェック
    if "profit_amount" not in df.columns or "pair" not in df.columns:
        print("❌ 必要なカラム (pair, profit_amount 等) が不足しています。")
        return pd.DataFrame()

    # 文字列クレンジング（通貨記号やカンマの除去）
    if df["profit_amount"].dtype == object:
        df["profit_amount"] = (
            df["profit_amount"].astype(str)
            .str.replace(" 円", "", regex=False)
            .str.replace(",", "", regex=False)
            .str.replace("+", "", regex=False)
            .str.strip()
            .astype(float)
        )

    # 重複排除（同じペア、エントリー時間、方向、建値）
    subset_cols = [c for c in ["pair", "entry_time", "direction", "entry_price"] if c in df.columns]
    if subset_cols:
        initial_len = len(df)
        df = df.drop_duplicates(subset=subset_cols, keep="last").copy()
        dedup_len = len(df)
        if initial_len != dedup_len:
            print(f"🧹 重複レコード排除: {initial_len:,} 件 → {dedup_len:,} 件 ({initial_len - dedup_len} 件除外)")

    # 日時パース
    if "timestamp" in df.columns:
        df["timestamp_dt"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    else:
        df["timestamp_dt"] = pd.to_datetime(df["entry_time"], utc=True, errors="coerce")

    if "entry_time" in df.columns:
        df["entry_time_dt"] = pd.to_datetime(df["entry_time"], utc=True, errors="coerce")
    else:
        df["entry_time_dt"] = df["timestamp_dt"]

    # タイムゾーン（JST）および時間属性の導出
    df["jst_entry_dt"] = df["entry_time_dt"].dt.tz_convert("Asia/Tokyo")
    df["jst_exit_dt"] = df["timestamp_dt"].dt.tz_convert("Asia/Tokyo")
    df["jst_hour"] = df["jst_entry_dt"].dt.hour
    df["day_of_week"] = df["jst_entry_dt"].dt.day_name()
    df["month"] = df["jst_entry_dt"].dt.strftime("%Y-%m")

    # 保有時間 (分)
    df["hold_time_min"] = (df["timestamp_dt"] - df["entry_time_dt"]).dt.total_seconds() / 60.0
    df["hold_time_min"] = df["hold_time_min"].apply(lambda x: max(0.0, x) if not pd.isna(x) else 0.0)

    # pips計算
    if "entry_price" in df.columns and "exit_price" in df.columns:
        df["pnl_pips"] = df.apply(calc_pips, axis=1)
    else:
        df["pnl_pips"] = 0.0

    # フィルター適用
    if pair_filter:
        df = df[df["pair"] == pair_filter].copy()
        print(f"🔍 通貨ペアフィルター適用: {pair_filter} ({len(df):,} 件)")
    if strategy_filter and "strategy" in df.columns:
        df = df[df["strategy"] == strategy_filter].copy()
        print(f"🔍 戦略フィルター適用: {strategy_filter} ({len(df):,} 件)")

    # ソート
    df = df.sort_values("entry_time_dt").reset_index(drop=True)
    return df


# ==============================================================================
# 3. 総合分析エンジン（各セクション）
# ==============================================================================

def analyze_and_format(df: pd.DataFrame, min_streak: int = 4) -> Tuple[str, Dict[str, Any]]:
    """全セクションの分析を行い、人間が読みやすい文字列レポートおよび辞書データを生成する"""
    if df.empty:
        return "データがありません。", {}

    lines = []
    def p(text=""): lines.append(text)

    p("=" * 105)
    p(" 🚀 FX AUTOMATED TRADING SYSTEM - COMPREHENSIVE PERFORMANCE & RISK AUDIT")
    p("=" * 105)
    p(f"・解析日時       : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    p(f"・データ期間 (JST): {df['jst_entry_dt'].min().strftime('%Y-%m-%d %H:%M')} ～ {df['jst_exit_dt'].max().strftime('%Y-%m-%d %H:%M')}")
    p(f"・総解析トレード数: {len(df):,} 件")
    p("=" * 105)

    report_dict: Dict[str, Any] = {}

    # --------------------------------------------------------------------------
    # 【1. 全体総合パフォーマンス (Executive Summary)】
    # --------------------------------------------------------------------------
    tot = calculate_metrics(df)
    report_dict["overall"] = tot

    p("\n【1. 全体総合パフォーマンス (Executive Summary)】")
    p("-" * 105)
    p(f"  ・ 総トレード数 (Total Trades)     : {tot['trades']:,} 回 (勝: {tot['wins']:,} / 負: {tot['losses']:,} / 分: {tot['evens']:,})")
    p(f"  ・ 全体勝率 (Win Rate)             : {tot['win_rate']:.2%}")
    p(f"  ・ プロフィットファクター (PF)     : {tot['pf']:.3f}")
    p(f"  ・ 純損益合計 (Net Total PnL)      : {tot['total_pnl']:+,.0f} JPY")
    p(f"  ・ 総利益 (Gross Profit)           : +{tot['gross_profit']:,.0f} JPY")
    p(f"  ・ 総損失 (Gross Loss)             : -{tot['gross_loss']:,.0f} JPY")
    p(f"  ・ 期待値 / 回 (Expected Value)   : {tot['ev']:+,.1f} JPY / trade (平均 {tot['avg_pips']:+.1f} pips)")
    p(f"  ・ 平均利益 (Avg Win)              : +{tot['avg_win']:,.0f} JPY")
    p(f"  ・ 平均損失 (Avg Loss)             : -{tot['avg_loss']:,.0f} JPY")
    p(f"  ・ リスクリワード比 (Payoff Ratio) : {tot['rr_ratio']:.2f}")
    p(f"  ・ 最大ドローダウン額 (Max DD)     : {tot['mdd_amount']:,.0f} JPY ({tot['mdd_pct']:.2f} %)")
    p(f"  ・ 平均 / 中央値 保有時間          : {tot['avg_hold_min']:.1f} 分 / {tot['med_hold_min']:.1f} 分")
    p(f"  ・ 最大連続勝数 / 最大連続損切数   : {tot['max_consecutive_wins']} 連勝 / {tot['max_consecutive_losses']} 連敗")

    # --------------------------------------------------------------------------
    # 【2. 防衛壁・ルール適合性監査 (Guard Audit)】
    # --------------------------------------------------------------------------
    p("\n" + "=" * 105)
    p("【2. 防衛壁・ルール適合性監査 (Guard & Barrier Compliance Audit)】")
    p("-" * 105)

    # ① 魔の時間帯 (JST 01:00 - 08:59)
    forbidden_trades = df[(df["jst_hour"] >= 1) & (df["jst_hour"] <= 8)]
    forbid_count = len(forbidden_trades)
    if forbid_count == 0:
        p("  [1] 時間帯ガード (JST 01:00-08:59) : 🛡️ 適合 (100% 遮断 / 違反エントリー 0件)")
    else:
        p(f"  [1] 時間帯ガード (JST 01:00-08:59) : ⚠️ 警告 ({forbid_count} 件の禁止時間外エントリーが検出されました)")

    # ② SL下限バリアチェック (10.0 pips)
    sl_trades = df[df["profit_amount"] < 0]
    if not sl_trades.empty and "pnl_pips" in sl_trades.columns:
        sl_pips_abs = sl_trades["pnl_pips"].abs()
        min_sl_recorded = sl_pips_abs.min()
        under_10pips_count = len(sl_trades[sl_pips_abs < 9.9])
        if under_10pips_count == 0:
            p(f"  [2] SL下限バリア (min_sl >= 10.0p) : 🛡️ 適合 (最小損切り幅: {min_sl_recorded:.1f} pips / 10p未満 0件)")
        else:
            p(f"  [2] SL下限バリア (min_sl >= 10.0p) : ⚠️ 警告 (10p未満の損切りが {under_10pips_count} 件検出されました)")
    else:
        p("  [2] SL下限バリア                   : 判定対象データなし (損切りトレード 0件)")

    # ③ 金曜夜間ガード (金曜 20:00 JST以降の新規エントリー)
    fri_night_trades = df[(df["day_of_week"] == "Friday") & (df["jst_hour"] >= 20)]
    if len(fri_night_trades) == 0:
        p("  [3] 週末夜間ガード (金曜20時以降)  : 🛡️ 適合 (持ち越しリスク完全遮断 / 0件)")
    else:
        p(f"  [3] 週末夜間ガード (金曜20時以降)  : ⚠️ 検出 ({len(fri_night_trades)} 件のエントリー)")

    # --------------------------------------------------------------------------
    # 【3. 通貨ペア別パフォーマンス (Currency Pair Breakdown)】
    # --------------------------------------------------------------------------
    p("\n" + "=" * 105)
    p("【3. 通貨ペア別パフォーマンス (Currency Pair Breakdown)】")
    p("-" * 105)
    p(f"{'通貨ペア (Pair)':10s} | {'件数':4s} | {'勝率':7s} | {'純損益 (PnL)':15s} | {'PF':6s} | {'平均利益':9s} | {'平均損失':9s} | {'平均pips':8s} | {'最大DD':9s}")
    p("-" * 105)
    
    pair_records = {}
    for pair, grp in df.groupby("pair"):
        m = calculate_metrics(grp)
        pair_records[pair] = m
        pf_str = f"{m['pf']:.2f}" if m["pf"] != float("inf") else "∞"
        p(f"{pair:10s} | {m['trades']:4d} | {m['win_rate']:6.1%} | {m['total_pnl']:+13,.0f} 円 | {pf_str:>6s} | {m['avg_win']:7,.0f} 円 | {m['avg_loss']:7,.0f} 円 | {m['avg_pips']:+6.1f} p | {m['mdd_amount']:7,.0f} 円")
    report_dict["pairs"] = pair_records

    # --------------------------------------------------------------------------
    # 【4. 戦略別 & 売買方向別パフォーマンス (Strategy & Direction Breakdown)】
    # --------------------------------------------------------------------------
    p("\n" + "=" * 105)
    p("【4. 戦略 & 売買方向別パフォーマンス (Strategy & Direction Breakdown)】")
    p("-" * 105)

    if "strategy" in df.columns:
        p("・戦略別 (Strategy):")
        for strat, grp in df.groupby("strategy"):
            s = calculate_metrics(grp)
            pf_str = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "∞"
            p(f"   [{strat:12s}] 件数: {s['trades']:4d} | 勝率: {s['win_rate']:6.1%} | 純利益: {s['total_pnl']:+12,.0f} 円 | PF: {pf_str:>5s} | 期待値: {s['ev']:+7,.1f} 円")

    if "direction" in df.columns:
        p("\n・売買方向別 (Direction):")
        for direction, grp in df.groupby("direction"):
            d = calculate_metrics(grp)
            pf_str = f"{d['pf']:.2f}" if d["pf"] != float("inf") else "∞"
            p(f"   [{direction.upper():12s}] 件数: {d['trades']:4d} | 勝率: {d['win_rate']:6.1%} | 純利益: {d['total_pnl']:+12,.0f} 円 | PF: {pf_str:>5s} | 期待値: {d['ev']:+7,.1f} 円")

    # --------------------------------------------------------------------------
    # 【5. 決済理由別詳細パフォーマンス (Exit Reason Breakdown)】
    # --------------------------------------------------------------------------
    p("\n" + "=" * 105)
    p("【5. 決済理由別パフォーマンス (Exit Reason Breakdown)】")
    p("-" * 105)
    p(f"{'決済理由 (Exit Reason)':28s} | {'件数':5s} | {'構成比':6s} | {'勝率':7s} | {'損益合計 (PnL)':16s} | {'1件平均損益':11s} | {'平均保有時間':10s}")
    p("-" * 105)

    reason_records = {}
    if "reason" in df.columns:
        for reason, grp in df.groupby("reason"):
            r = calculate_metrics(grp)
            reason_records[reason] = r
            ratio = len(grp) / len(df) if len(df) > 0 else 0.0
            p(f"{reason:28s} | {r['trades']:5d} | {ratio:5.1%} | {r['win_rate']:6.1%} | {r['total_pnl']:+14,.0f} 円 | {r['ev']:+9,.1f} 円 | {r['avg_hold_min']:8.1f} 分")
    report_dict["reasons"] = reason_records

    # --------------------------------------------------------------------------
    # 【6. AIスコア階層別パフォーマンス (AI Score Tier Breakdown)】
    # --------------------------------------------------------------------------
    if "ai_score" in df.columns and not df["ai_score"].isna().all():
        p("\n" + "=" * 105)
        p("【6. AIスコア階層別パフォーマンス (AI Score Tier Breakdown)】")
        p("-" * 105)
        df_score = df.copy()
        bins = [-1.0, 0.549, 0.699, 0.849, 1.01]
        labels = ["< 0.55 (Low)", "0.55 - 0.69 (Mid)", "0.70 - 0.84 (High/Golden)", "0.85 - 1.00 (Max)"]
        df_score["score_tier"] = pd.cut(df_score["ai_score"], bins=bins, labels=labels)
        
        for tier, grp in df_score.groupby("score_tier", observed=False):
            if len(grp) > 0:
                sc = calculate_metrics(grp)
                pf_str = f"{sc['pf']:.2f}" if sc["pf"] != float("inf") else "∞"
                ratio = len(grp) / len(df)
                p(f"  ・ Tier {str(tier):24s} | 件数: {sc['trades']:4d} ({ratio:4.1%}) | 勝率: {sc['win_rate']:6.1%} | 純利益: {sc['total_pnl']:+12,.0f} 円 | PF: {pf_str:>5s} | 期待値: {sc['ev']:+7,.1f} 円")

    # --------------------------------------------------------------------------
    # 【7. 曜日別 & JST時間帯別パフォーマンス (Day & Time-window Breakdown)】
    # --------------------------------------------------------------------------
    p("\n" + "=" * 105)
    p("【7. 曜日別 & JST時間帯別パフォーマンス (Day of Week & Time-window Breakdown)】")
    p("-" * 105)

    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    p("・曜日別 (JST):")
    for day in day_order:
        grp = df[df["day_of_week"] == day]
        if len(grp) > 0:
            dm = calculate_metrics(grp)
            pf_str = f"{dm['pf']:.2f}" if dm["pf"] != float("inf") else "∞"
            p(f"   [{day:9s}] 件数: {dm['trades']:4d} | 勝率: {dm['win_rate']:6.1%} | 純利益: {dm['total_pnl']:+12,.0f} 円 | PF: {pf_str:>5s} | 平均損益: {dm['ev']:+7,.1f} 円")

    p("\n・時間帯別 (JST Session Windows):")
    time_windows = [
        ("09:00 - 11:59 (東京前場 / 仲値)", 9, 11),
        ("12:00 - 15:59 (東京後場 / 欧州前)", 12, 15),
        ("16:00 - 19:59 (ロンドン前場 / メイン)", 16, 19),
        ("20:00 - 23:59 (NY前場 / 指標ラッシュ)", 20, 23),
        ("00:00 - 00:59 (NYミッドナイト)", 0, 0)
    ]
    for win_name, s_h, e_h in time_windows:
        grp = df[(df["jst_hour"] >= s_h) & (df["jst_hour"] <= e_h)]
        if len(grp) > 0:
            tm = calculate_metrics(grp)
            pf_str = f"{tm['pf']:.2f}" if tm["pf"] != float("inf") else "∞"
            p(f"   [{win_name:32s}] 件数: {tm['trades']:4d} | 勝率: {tm['win_rate']:6.1%} | 純利益: {tm['total_pnl']:+12,.0f} 円 | PF: {pf_str:>5s}")

    # --------------------------------------------------------------------------
    # 【8. 連続損切り・連敗クラスタ分析 (Consecutive Loss Streaks)】
    # --------------------------------------------------------------------------
    p("\n" + "=" * 105)
    p(f"【8. 連続損切り・連敗クラスタ分析 (Consecutive Losses >= {min_streak})】")
    p("-" * 105)

    df_loss = df.copy()
    df_loss["is_loss"] = df_loss["profit_amount"] < 0

    streaks = []
    curr_streak = []
    for idx, row in df_loss.iterrows():
        if row["is_loss"]:
            curr_streak.append(row)
        else:
            if len(curr_streak) >= min_streak:
                streaks.append(pd.DataFrame(curr_streak))
            curr_streak = []
    if len(curr_streak) >= min_streak:
        streaks.append(pd.DataFrame(curr_streak))

    p(f"・検出された連続 {min_streak} 回以上損失クラスタ数: {len(streaks)} 件")
    for i, s in enumerate(streaks[:5], 1): # 上位5件表示
        s_loss_sum = s["profit_amount"].sum()
        s_start = s["jst_entry_dt"].min().strftime("%m/%d %H:%M")
        s_end = s["jst_exit_dt"].max().strftime("%m/%d %H:%M")
        pairs_str = ", ".join([f"{k}:{v}" for k, v in s["pair"].value_counts().items()])
        p(f"   [クラスタ #{i}] {len(s)} 連敗 | 期間: {s_start} ～ {s_end} | 損失合計: {s_loss_sum:+,.0f} 円 | 対象: {pairs_str}")

    if len(streaks) > 5:
        p(f"   ... 他 {len(streaks) - 5} 件のクラスタがあります。")

    # --------------------------------------------------------------------------
    # 【9. 月別推移 (Monthly Performance Breakdown)】
    # --------------------------------------------------------------------------
    p("\n" + "=" * 105)
    p("【9. 月別パフォーマンス推移 (Monthly Performance Breakdown)】")
    p("-" * 105)
    p(f"{'対象月 (Month)':12s} | {'件数':4s} | {'勝数':4s} | {'負数':4s} | {'勝率':7s} | {'純利益 (PnL)':15s} | {'PF':6s} | {'平均利益':9s} | {'平均損失':9s} | {'月間MDD':9s}")
    p("-" * 105)

    monthly_records = {}
    for month, grp in df.groupby("month"):
        m = calculate_metrics(grp)
        monthly_records[month] = m
        pf_str = f"{m['pf']:.2f}" if m["pf"] != float("inf") else "∞"
        p(f"{month:12s} | {m['trades']:4d} | {m['wins']:4d} | {m['losses']:4d} | {m['win_rate']:6.1%} | {m['total_pnl']:+13,.0f} 円 | {pf_str:>6s} | {m['avg_win']:7,.0f} 円 | {m['avg_loss']:7,.0f} 円 | {m['mdd_amount']:7,.0f} 円")
    report_dict["monthly"] = monthly_records

    p("=" * 105)
    p(" 🏁 総合パフォーマンス＆リスク監査 完了")
    p("=" * 105)

    formatted_text = "\n".join(lines)
    return formatted_text, report_dict


# ==============================================================================
# 4. エクスポーター (Markdown / JSON 保存)
# ==============================================================================

def export_markdown_report(report_text: str, output_path: Path):
    """Markdown形式でレポートを保存する"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    md_content = f"# 📊 FXsys バックテスト総合分析レポート\n\n```text\n{report_text}\n```\n"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"📄 Markdown レポートを出力しました: {output_path}")


def export_json_report(report_dict: Dict[str, Any], output_path: Path):
    """JSON形式で集計結果を出力する"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2, ensure_ascii=False)
    print(f"💾 JSON サマリーを出力しました: {output_path}")


# ==============================================================================
# 5. メインエントリーポイント (CLI)
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="FXsys Comprehensive Performance & Risk Analyzer - 多角的なバックテスト・取引ログ分析ツール"
    )
    parser.add_argument("--csv", type=str, default=None, help="分析対象のCSVファイルまたはディレクトリパス")
    parser.add_argument("--log_dir", type=str, default="logs", help="ログディレクトリ名 (デフォルト: logs)")
    parser.add_argument("--pair", type=str, default=None, help="特定の通貨ペアのみに絞り込む (例: USD_JPY)")
    parser.add_argument("--strategy", type=str, default=None, help="特定の戦略のみに絞り込む (例: trend)")
    parser.add_argument("--min_streak", type=int, default=4, help="連続損失クラスタの閾値回数 (デフォルト: 4)")
    parser.add_argument("--export_md", type=str, default=None, help="Markdownレポートの出力先パス (省略時は自動命名)")
    parser.add_argument("--export_json", type=str, default=None, help="JSONサマリーの出力先パス")
    parser.add_argument("--no_export", action="store_true", help="ファイルへのレポート出力をスキップし画面表示のみにする")

    args = parser.parse_args()

    # データ読み込み
    df = load_dataset(
        target_path=args.csv,
        log_dir=args.log_dir,
        pair_filter=args.pair,
        strategy_filter=args.strategy
    )

    if df.empty:
        print("❌ 分析対象データが存在しないため終了します。")
        sys.exit(1)

    # 分析実行
    report_text, report_dict = analyze_and_format(df, min_streak=args.min_streak)
    print(report_text)

    # エクスポート処理
    if not args.no_export:
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_dir = Path(__file__).resolve().parent
        
        md_path = Path(args.export_md) if args.export_md else base_dir.parent / "data" / "reports" / f"comprehensive_analysis_{timestamp_str}.md"
        export_markdown_report(report_text, md_path)

        if args.export_json:
            export_json_report(report_dict, Path(args.export_json))


if __name__ == "__main__":
    main()
