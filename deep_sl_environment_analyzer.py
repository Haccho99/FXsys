"""
deep_sl_environment_analyzer.py - 連続SLクラスタと通常トレードのATR・相関・ボラティリティ・値動き詳細比較
"""
import glob
from pathlib import Path
import pandas as pd
import numpy as np

def run_deep_analysis():
    # 1. 仮想トレードログの読み込み
    files = sorted(glob.glob("logs/virtual_trade_log_*.csv"))
    dfs = [pd.read_csv(f) for f in files if Path(f).stat().st_size > 0]
    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df["duration_min"] = (df["timestamp"] - df["entry_time"]).dt.total_seconds() / 60.0
    
    def calc_pips(row):
        is_jpy = "JPY" in row["pair"]
        pip_unit = 0.01 if is_jpy else 0.0001
        diff = (row["exit_price"] - row["entry_price"]) if row["direction"] == "long" else (row["entry_price"] - row["exit_price"])
        return diff / pip_unit

    df["pnl_pips"] = df.apply(calc_pips, axis=1)
    df["is_sl"] = (df["reason"] == "SL") | (df["profit_amount"] <= 0)
    df = df.sort_values("entry_time").reset_index(drop=True)

    # 2. リスクログの読み込みと結合
    risk_file = Path("logs/risk_log_20260807.csv")
    if risk_file.exists():
        rdf = pd.read_csv(risk_file)
        print(f"Trade log count: {len(df)}, Risk log count: {len(rdf)}")
        min_len = min(len(df), len(rdf))
        df = df.iloc[:min_len].copy()
        rdf = rdf.iloc[:min_len].copy()
        df["atr_pips"] = rdf["atr_pips"].values
        df["exposure_ratio"] = rdf["exposure_ratio"].values
        df["corr_adjust"] = rdf["corr_adjust"].values
    else:
        df["atr_pips"] = np.nan

    # 3. エントリー間隔の計算 (前回の同一ペア決済から次のエントリーまでの時間)
    df["prev_exit_time"] = df.groupby("pair")["timestamp"].shift(1)
    df["reentry_interval_min"] = (df["entry_time"] - df["prev_exit_time"]).dt.total_seconds() / 60.0

    # 4. ペア別の連続5回以上SLクラスタ抽出
    streaks_by_pair = []
    cluster_indices = []
    
    for pair in df["pair"].unique():
        pdf = df[df["pair"] == pair].copy()
        curr = []
        for idx, row in pdf.iterrows():
            if row["is_sl"]:
                curr.append(idx)
            else:
                if len(curr) >= 5:
                    streaks_by_pair.append((pair, curr))
                    cluster_indices.extend(curr)
                curr = []
        if len(curr) >= 5:
            streaks_by_pair.append((pair, curr))
            cluster_indices.extend(curr)

    df["in_sl_cluster"] = df.index.isin(cluster_indices)
    sl_c = df[df["in_sl_cluster"]]
    normal = df[~df["in_sl_cluster"]]

    print("==========================================================================")
    print("【連続SL発生環境 vs 通常環境の計量比較】")
    print("==========================================================================")
    
    metrics = {
        "平均ATR (pips)": (sl_c["atr_pips"].mean(), normal["atr_pips"].mean()),
        "中央値ATR (pips)": (sl_c["atr_pips"].median(), normal["atr_pips"].median()),
        "平均保有時間 (分)": (sl_c["duration_min"].mean(), normal["duration_min"].mean()),
        "中央値保有時間 (分)": (sl_c["duration_min"].median(), normal["duration_min"].median()),
        "平均再エントリー間隔 (分)": (sl_c[sl_c["reentry_interval_min"] >= 0]["reentry_interval_min"].mean(), normal[normal["reentry_interval_min"] >= 0]["reentry_interval_min"].mean()),
        "中央値再エントリー間隔 (分)": (sl_c[sl_c["reentry_interval_min"] >= 0]["reentry_interval_min"].median(), normal[normal["reentry_interval_min"] >= 0]["reentry_interval_min"].median()),
        "即座再エントリー率 (15分以内 %)": ((sl_c["reentry_interval_min"] <= 15).mean() * 100, (normal["reentry_interval_min"] <= 15).mean() * 100),
        "平均損益 (円)": (sl_c["profit_amount"].mean(), normal["profit_amount"].mean()),
        "平均被SL幅 (pips)": (sl_c["pnl_pips"].mean(), normal[normal["is_sl"]]["pnl_pips"].mean()),
    }
    
    comp_df = pd.DataFrame(metrics, index=["連続SL群 (Clusters)", "通常トレード群 (Normal)"]).T
    print(comp_df.round(2).to_string())

    print("\n==========================================================================")
    print("【主要8大クラスタの値動きパケット・相場環境の定性・定量分析】")
    print("==========================================================================")

    for i, (pair, indices) in enumerate(streaks_by_pair, 1):
        cdf = df.loc[indices]
        start_t = cdf['entry_time'].min()
        end_t = cdf['timestamp'].max()
        tot_loss = cdf['profit_amount'].sum()
        avg_atr = cdf['atr_pips'].mean() if 'atr_pips' in cdf.columns else 0.0
        
        # 価格変動幅 (High/Low Span)
        all_prices = pd.concat([cdf["entry_price"], cdf["exit_price"]])
        price_range = all_prices.max() - all_prices.min()
        pip_unit = 0.01 if "JPY" in pair else 0.0001
        range_pips = price_range / pip_unit
        
        print(f"\n▶ クラスタ #{i}: [{pair}] 連続 {len(cdf)} 回 SL")
        print(f"   期間: {start_t:%Y-%m-%d %H:%M} ~ {end_t:%Y-%m-%d %H:%M} (約 {(end_t - start_t).total_seconds()/3600:.1f} 時間)")
        print(f"   累積損失: {tot_loss:,.0f} 円 | レンジ値幅: {range_pips:.1f} pips | 平均ATR: {avg_atr:.2f} pips")
        
        # 方向の切り替わり回数
        directions = cdf["direction"].tolist()
        switches = sum(1 for d1, d2 in zip(directions[:-1], directions[1:]) if d1 != d2)
        print(f"   - エントリー方向: {directions} (ドテン・方向反転: {switches} 回)")
        
        for idx_row, r in cdf.iterrows():
            ent_t = r['entry_time']
            ext_t = r['timestamp']
            print(f"     * {ent_t:%m/%d %H:%M} -> {ext_t:%H:%M} ({r['duration_min']:.0f}分) | {r['direction']:5s} | Ent: {r['entry_price']:.3f} -> Ext: {r['exit_price']:.3f} | {r['pnl_pips']:+6.1f} pips ({r['profit_amount']:+6.0f}円) | ATR:{r.get('atr_pips', 0):.1f}p | 再突入間隔: {r.get('reentry_interval_min', 0):.0f}分")

if __name__ == "__main__":
    run_deep_analysis()
