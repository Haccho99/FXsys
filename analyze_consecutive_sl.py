"""
analyze_consecutive_sl.py - 勝率63%バックテストデータの詳細解析と連続5回以上SL箇所の特定・環境要因分析
"""
import glob
from pathlib import Path
import pandas as pd
import numpy as np
from datetime import datetime

def load_all_virtual_trades(log_dir: Path) -> pd.DataFrame:
    files = sorted(glob.glob(str(log_dir / "virtual_trade_log_*.csv")))
    if not files:
        print("No virtual trade logs found!")
        return pd.DataFrame()
    
    dfs = []
    for f in files:
        try:
            d = pd.read_csv(f)
            if not d.empty:
                dfs.append(d)
        except Exception as e:
            pass
            
    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df = df.sort_values("entry_time").reset_index(drop=True)
    return df

def analyze():
    log_dir = Path("logs")
    df = load_all_virtual_trades(log_dir)
    print(f"Total loaded trades: {len(df)}")
    
    if df.empty:
        return

    # 基本指標
    wins = df[df["profit_amount"] > 0]
    losses = df[df["profit_amount"] <= 0]
    sl_trades = df[df["reason"] == "SL"]
    
    win_rate = len(wins) / len(df)
    total_pnl = df["profit_amount"].sum()
    total_profit = wins["profit_amount"].sum()
    total_loss = abs(losses["profit_amount"].sum())
    pf = total_profit / total_loss if total_loss > 0 else 0
    
    print("\n==================================================")
    print(f"【全体概要】")
    print(f"期間: {df['entry_time'].min()} ~ {df['entry_time'].max()}")
    print(f"総トレード数: {len(df)}")
    print(f"勝率: {win_rate:.2%} ({len(wins)}勝 / {len(losses)}敗)")
    print(f"総損益: {total_pnl:,.0f} 円")
    print(f"プロフィットファクター (PF): {pf:.2f}")
    print(f"決済理由の内訳:")
    print(df["reason"].value_counts().to_string())
    print("==================================================\n")

    # 1. 全体時系列での連続SL検出 (ポートフォリオ全体)
    df["is_sl"] = (df["reason"] == "SL") | (df["profit_amount"] <= 0)
    
    # 連続損失のカウント
    streaks = []
    current_streak = []
    
    for idx, row in df.iterrows():
        if row["is_sl"]:
            current_streak.append(row)
        else:
            if len(current_streak) >= 5:
                streaks.append(pd.DataFrame(current_streak))
            current_streak = []
            
    if len(current_streak) >= 5:
        streaks.append(pd.DataFrame(current_streak))

    print(f"【ポートフォリオ全体での連続5回以上損失 (SL) クラスタ数: {len(streaks)} 件】\n")
    for i, s in enumerate(streaks, 1):
        print(f"--- [クラスタ #{i}] 連続 {len(s)} 回 損失 ---")
        print(f"期間: {s['entry_time'].min()} -> {s['timestamp'].max()}")
        print(f"合計損失額: {s['profit_amount'].sum():,.0f} 円")
        print(f"対象ペア: {s['pair'].value_counts().to_dict()}")
        print(f"方向: {s['direction'].value_counts().to_dict()}")
        print(f"戦略: {s['strategy'].value_counts().to_dict()}")
        print(f"AIスコア平均: {s['ai_score'].mean():.2f}")
        print(s[["entry_time", "timestamp", "pair", "direction", "strategy", "entry_price", "exit_price", "profit_amount", "reason", "ai_score"]].to_string())
        print("\n")

    # 2. 通貨ペア単体での連続5回以上SL検出
    print("==================================================")
    print("【通貨ペア単体での連続5回以上 SL クラスタ】")
    pair_streaks = []
    for pair in df["pair"].unique():
        pdf = df[df["pair"] == pair].copy().sort_values("entry_time").reset_index(drop=True)
        p_streak = []
        for idx, row in pdf.iterrows():
            if row["is_sl"]:
                p_streak.append(row)
            else:
                if len(p_streak) >= 5:
                    pair_streaks.append((pair, pd.DataFrame(p_streak)))
                p_streak = []
        if len(p_streak) >= 5:
            pair_streaks.append((pair, pd.DataFrame(p_streak)))

    print(f"通貨ペア単体での連続5回以上損失クラスタ数: {len(pair_streaks)} 件\n")
    for i, (pair, ps) in enumerate(pair_streaks, 1):
        print(f"--- [ペア別クラスタ #{i}] {pair} 連続 {len(ps)} 回 損失 ---")
        print(f"期間: {ps['entry_time'].min()} -> {ps['timestamp'].max()}")
        print(f"合計損失額: {ps['profit_amount'].sum():,.0f} 円")
        print(f"方向内訳: {ps['direction'].value_counts().to_dict()}")
        print(f"戦略内訳: {ps['strategy'].value_counts().to_dict()}")
        print(ps[["entry_time", "timestamp", "direction", "strategy", "entry_price", "exit_price", "profit_amount", "reason", "ai_score"]].to_string())
        print("\n")

    # 3. 環境比較分析 (連続SL発生時 vs 通常トレード時)
    df["hour"] = df["entry_time"].dt.hour
    df["dayofweek"] = df["entry_time"].dt.dayofweek # 0=Mon, 4=Fri
    
    # 連続SLに含まれるインデックスのフラグ化
    sl_indices = set()
    for s in streaks:
        sl_indices.update(s.index)
        
    df["in_sl_cluster"] = df.index.isin(sl_indices)
    
    print("==================================================")
    print("【環境要因の比較分析: 連続SL群 vs 通常トレード群】")
    
    cluster_df = df[df["in_sl_cluster"]]
    normal_df = df[~df["in_sl_cluster"]]
    
    print("\n1. 時間帯分布 (Hour of Entry):")
    h_comp = pd.DataFrame({
        "SL_Cluster_Trades": cluster_df["hour"].value_counts(normalize=True).sort_index(),
        "Normal_Trades": normal_df["hour"].value_counts(normalize=True).sort_index()
    })
    print(h_comp.fillna(0).applymap(lambda x: f"{x:.1%}").to_string())

    print("\n2. 曜日分布 (Day of Week, 0=Mon, 4=Fri):")
    dow_comp = pd.DataFrame({
        "SL_Cluster_Trades": cluster_df["dayofweek"].value_counts(normalize=True).sort_index(),
        "Normal_Trades": normal_df["dayofweek"].value_counts(normalize=True).sort_index()
    })
    print(dow_comp.fillna(0).applymap(lambda x: f"{x:.1%}").to_string())

    print("\n3. 通貨ペア比率:")
    pair_comp = pd.DataFrame({
        "SL_Cluster_Trades": cluster_df["pair"].value_counts(normalize=True),
        "Normal_Trades": normal_df["pair"].value_counts(normalize=True)
    })
    print(pair_comp.fillna(0).applymap(lambda x: f"{x:.1%}").to_string())

    print("\n4. 戦略比率:")
    strat_comp = pd.DataFrame({
        "SL_Cluster_Trades": cluster_df["strategy"].value_counts(normalize=True),
        "Normal_Trades": normal_df["strategy"].value_counts(normalize=True)
    })
    print(strat_comp.fillna(0).applymap(lambda x: f"{x:.1%}").to_string())

    print("\n5. AIスコア分布:")
    print(f"SL_Cluster 平均AIスコア: {cluster_df['ai_score'].mean():.3f} (中央値: {cluster_df['ai_score'].median():.3f})")
    print(f"Normal 平均AIスコア: {normal_df['ai_score'].mean():.3f} (中央値: {normal_df['ai_score'].median():.3f})")

if __name__ == "__main__":
    analyze()
