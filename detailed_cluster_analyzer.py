"""
detailed_cluster_analyzer.py - 連続5回以上SLの詳細分析・値動き・トレード環境分析
"""
import glob
from pathlib import Path
import pandas as pd
import numpy as np

def load_all_trades():
    files = sorted(glob.glob("logs/virtual_trade_log_*.csv"))
    dfs = [pd.read_csv(f) for f in files if Path(f).stat().st_size > 0]
    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df["duration_min"] = (df["timestamp"] - df["entry_time"]).dt.total_seconds() / 60.0
    
    # pips calculation
    def calc_pips(row):
        is_jpy = "JPY" in row["pair"]
        pip_unit = 0.01 if is_jpy else 0.0001
        diff = (row["exit_price"] - row["entry_price"]) if row["direction"] == "long" else (row["entry_price"] - row["exit_price"])
        return diff / pip_unit

    df["pnl_pips"] = df.apply(calc_pips, axis=1)
    df["is_sl"] = (df["reason"] == "SL") | (df["profit_amount"] <= 0)
    df = df.sort_values("entry_time").reset_index(drop=True)
    return df

def analyze_all():
    df = load_all_trades()
    
    # 1. 連続SLクラスタの抽出（ポートフォリオ全体）
    streaks_portfolio = []
    curr = []
    for idx, row in df.iterrows():
        if row["is_sl"]:
            curr.append(row)
        else:
            if len(curr) >= 5:
                streaks_portfolio.append(pd.DataFrame(curr))
            curr = []
    if len(curr) >= 5:
        streaks_portfolio.append(pd.DataFrame(curr))

    # 2. 連続SLクラスタの抽出（通貨ペア別）
    streaks_by_pair = []
    for pair in df["pair"].unique():
        pdf = df[df["pair"] == pair].copy().sort_values("entry_time").reset_index(drop=True)
        curr = []
        for idx, row in pdf.iterrows():
            if row["is_sl"]:
                curr.append(row)
            else:
                if len(curr) >= 5:
                    streaks_by_pair.append((pair, pd.DataFrame(curr)))
                curr = []
        if len(curr) >= 5:
            streaks_by_pair.append((pair, pd.DataFrame(curr)))

    print("================================================================")
    print("【1. 全体概要と決済理由別統計】")
    print(f"総トレード数: {len(df)}")
    print(f"勝率: {(df['profit_amount'] > 0).mean():.2%}")
    print(f"総損益: {df['profit_amount'].sum():,.0f} 円")
    print(f"平均利益: {df[df['profit_amount'] > 0]['profit_amount'].mean():,.0f} 円")
    print(f"平均損失: {df[df['profit_amount'] <= 0]['profit_amount'].mean():,.0f} 円")
    print(f"平均保有時間 (全体): {df['duration_min'].mean():.1f} 分")
    print(f"平均保有時間 (SL決済): {df[df['reason'] == 'SL']['duration_min'].mean():.1f} 分")
    print(f"平均保有時間 (TP/TSL決済): {df[df['reason'] != 'SL']['duration_min'].mean():.1f} 分")
    print("================================================================\n")

    # 3. 通貨ペアごとの連続SLクラスタ詳細
    print("================================================================")
    print("【2. 通貨ペア別 連続5回以上SLクラスタ（全詳細）】")
    print(f"検出されたペア別クラスタ総数: {len(streaks_by_pair)} 件")
    print("================================================================\n")

    cluster_trade_indices = []
    for i, (pair, cdf) in enumerate(streaks_by_pair, 1):
        cluster_trade_indices.extend(cdf.index.tolist())
        start_t = cdf['entry_time'].min()
        end_t = cdf['timestamp'].max()
        tot_loss = cdf['profit_amount'].sum()
        avg_hold = cdf['duration_min'].mean()
        avg_loss_pips = cdf['pnl_pips'].mean()
        dir_dist = cdf['direction'].value_counts().to_dict()
        strat_dist = cdf['strategy'].value_counts().to_dict()
        
        print(f"▶ [クラスタ #{i}] {pair} | 連続 {len(cdf)} 回 SL | 損失計: {tot_loss:,.0f}円 | 期間: {start_t:%Y/%m/%d %H:%M} ~ {end_t:%Y/%m/%d %H:%M}")
        print(f"   方向: {dir_dist} | 戦略: {strat_dist} | 平均保有: {avg_hold:.1f}分 | 平均被pips: {avg_loss_pips:.1f} pips")
        print(f"   詳細レコード:")
        sub = cdf[["entry_time", "direction", "strategy", "entry_price", "exit_price", "pnl_pips", "profit_amount", "duration_min", "ai_score"]]
        for _, r in sub.iterrows():
            print(f"     - {r['entry_time']:%m/%d %H:%M} | {r['direction']:5s} | Ent:{r['entry_price']:.3f} -> Ext:{r['exit_price']:.3f} | {r['pnl_pips']:.1f} pips | {r['profit_amount']:,.0f}円 | 保有:{r['duration_min']:.0f}分 | AI:{r['ai_score']}")
        print("")

    # 4. 通常時 vs 連続SL時の環境比較
    df["in_sl_cluster"] = df.index.isin(cluster_trade_indices)
    sl_c = df[df["in_sl_cluster"]]
    normal = df[~df["in_sl_cluster"]]

    print("================================================================")
    print("【3. トレード環境比較分析 (連続SL発生時 vs 通常時)】")
    print("================================================================")
    
    # 曜日別発生率
    df["dow"] = df["entry_time"].dt.day_name()
    dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    dow_stat = pd.DataFrame({
        "SL_Cluster (%)": (sl_c["entry_time"].dt.day_name().value_counts(normalize=True) * 100).reindex(dow_order).fillna(0),
        "Normal (%)": (normal["entry_time"].dt.day_name().value_counts(normalize=True) * 100).reindex(dow_order).fillna(0)
    })
    print("\n[曜日分布]")
    print(dow_stat.round(1).to_string())

    # 時間帯（市場セッション）別発生率
    def get_session(h):
        if 0 <= h < 7: return "01. Tokyo/Asia (00-06 UTC / 09-15 JST)"
        elif 7 <= h < 12: return "02. London Open (07-11 UTC / 16-20 JST)"
        elif 12 <= h < 16: return "03. NY Overlap (12-15 UTC / 21-24 JST)"
        elif 16 <= h < 21: return "04. NY Afternoon (16-20 UTC / 01-05 JST)"
        else: return "05. Roll Over (21-23 UTC / 06-08 JST)"
        
    df["session"] = df["entry_time"].dt.hour.apply(get_session)
    sl_c["session"] = sl_c["entry_time"].dt.hour.apply(get_session)
    normal["session"] = normal["entry_time"].dt.hour.apply(get_session)
    
    sess_stat = pd.DataFrame({
        "SL_Cluster (%)": (sl_c["session"].value_counts(normalize=True) * 100).sort_index(),
        "Normal (%)": (normal["session"].value_counts(normalize=True) * 100).sort_index()
    })
    print("\n[市場セッション分布 (UTC)]")
    print(sess_stat.round(1).to_string())

    # 戦略別
    strat_stat = pd.DataFrame({
        "SL_Cluster (%)": (sl_c["strategy"].value_counts(normalize=True) * 100),
        "Normal (%)": (normal["strategy"].value_counts(normalize=True) * 100)
    })
    print("\n[戦略比率]")
    print(strat_stat.round(1).to_string())

    # 保有時間
    print("\n[保有時間 (分)]")
    print(f"SL_Cluster 平均: {sl_c['duration_min'].mean():.1f}分 (中央値: {sl_c['duration_min'].median():.1f}分)")
    print(f"Normal 平均: {normal['duration_min'].mean():.1f}分 (中央値: {normal['duration_min'].median():.1f}分)")

if __name__ == "__main__":
    analyze_all()
