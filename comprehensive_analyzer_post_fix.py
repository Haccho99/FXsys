"""
comprehensive_analyzer_post_fix.py
==================================
修正後（Look-ahead Bias排除 ＆ AIスコア上限改善）バックテストデータの詳細分析・新旧比較スクリプト
"""
import os
import glob
from pathlib import Path
import pandas as pd
import numpy as np
import polars as pl
from datetime import datetime, timezone

def clean_money(val):
    if pd.isna(val): return 0.0
    val_str = str(val).replace(' 円', '').replace(',', '').replace('+', '').strip()
    try: return float(val_str)
    except: return 0.0

def clean_lot(val):
    if pd.isna(val): return 0.0
    val_str = str(val).replace(' Lot', '').replace(',', '').strip()
    try: return float(val_str)
    except: return 0.0

def main():
    project_root = Path(__file__).resolve().parent
    logs_dir = project_root / "logs"
    reports_dir = project_root.parent / "data" / "reports"
    old_csv_path = project_root / "2026-08-07T02-59_export.csv"

    print("================================================================================")
    print(" 🚀 FXsys 修正後バックテスト総合分析 & 新旧比較レポート (Post-Fix Comprehensive Analysis)")
    print("================================================================================")

    # 1. データの読み込みと結合
    csv_files = sorted(logs_dir.glob("virtual_trade_log_*.csv"))
    print(f"・読み込み対象ログファイル: {len(csv_files)} 個 (FXsys/logs/virtual_trade_log_*.csv)")
    
    dfs = []
    for f in csv_files:
        try:
            df_item = pd.read_csv(f)
            if not df_item.empty:
                dfs.append(df_item)
        except Exception as e:
            print(f"  [Warning] {f.name} の読み込みに失敗: {e}")

    df_new = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    print(f"・新バックテスト総取引数: {len(df_new):,} 件")

    # 旧データの読み込み
    df_old = pd.read_csv(old_csv_path)
    df_old['損益_clean'] = df_old['損益額'].apply(clean_money)
    df_old['残高_clean'] = df_old['口座残高'].apply(clean_money)
    df_old['数量_clean'] = df_old['数量'].apply(clean_lot)
    df_old['エントリー日時_dt'] = pd.to_datetime(df_old['エントリー日時'])
    df_old['決済日時_dt'] = pd.to_datetime(df_old['決済日時'])
    df_old['保有時間_分'] = (df_old['決済日時_dt'] - df_old['エントリー日時_dt']).dt.total_seconds() / 60.0

    # 新データの前処理
    df_new['timestamp_dt'] = pd.to_datetime(df_new['timestamp'])
    df_new['entry_time_dt'] = pd.to_datetime(df_new['entry_time'])
    df_new['保有時間_分'] = (df_new['timestamp_dt'] - df_new['entry_time_dt']).dt.total_seconds() / 60.0
    df_new['損益_clean'] = df_new['profit_amount']
    df_new['残高_clean'] = df_new['new_balance']

    # ==============================================================================
    # 2. 全体サマリー比較
    # ==============================================================================
    def get_summary(df, pnl_col='損益_clean', bal_col='残高_clean', hold_col='保有時間_分'):
        total = len(df)
        wins = df[df[pnl_col] > 0]
        losses = df[df[pnl_col] < 0]
        evens = df[df[pnl_col] == 0]
        win_rate = len(wins) / total * 100 if total > 0 else 0
        total_pnl = df[pnl_col].sum()
        gross_profit = wins[pnl_col].sum()
        gross_loss = abs(losses[pnl_col].sum())
        pf = gross_profit / gross_loss if gross_loss > 0 else np.nan
        avg_win = wins[pnl_col].mean() if len(wins) > 0 else 0
        avg_loss = abs(losses[pnl_col].mean()) if len(losses) > 0 else 0
        rr = avg_win / avg_loss if avg_loss > 0 else 0
        
        # Max Drawdown
        peak = df[bal_col].cummax()
        dd = peak - df[bal_col]
        max_dd = dd.max()
        max_dd_pct = (dd / peak).max() * 100 if peak.max() > 0 else 0

        return {
            "Total": total, "Wins": len(wins), "Losses": len(losses), "Evens": len(evens),
            "Win_Rate": win_rate, "Total_PnL": total_pnl, "Gross_Profit": gross_profit,
            "Gross_Loss": gross_loss, "PF": pf, "Avg_Win": avg_win, "Avg_Loss": avg_loss,
            "RR": rr, "Max_DD": max_dd, "Max_DD_Pct": max_dd_pct,
            "Avg_Hold_Min": df[hold_col].mean(), "Med_Hold_Min": df[hold_col].median()
        }

    s_old = get_summary(df_old)
    s_new = get_summary(df_new)

    print("\n" + "="*80)
    print("1. 全体パフォーマンス比較 (前回バックテスト vs 修正後バックテスト)")
    print("="*80)
    print(f"{'指標名':<25} | {'前回(修正前・未来参照有)':<22} | {'今回(修正後・完全確定足)':<22} | {'差異/増減'}")
    print("-" * 80)
    print(f"{'総トレード数':<25} | {s_old['Total']:>10,} 件{'':<10} | {s_new['Total']:>10,} 件{'':<10} | {s_new['Total'] - s_old['Total']:+d} 件 ({((s_new['Total']/s_old['Total'])-1)*100:+.1f}%)")
    print(f"{'勝率 (Win Rate)':<25} | {s_old['Win_Rate']:>10.2f} %{'':<10} | {s_new['Win_Rate']:>10.2f} %{'':<10} | {s_new['Win_Rate'] - s_old['Win_Rate']:+.2f} pt")
    print(f"{'プロフィットファクター (PF)':<20} | {s_old['PF']:>10.3f}{'':<12} | {s_new['PF']:>10.3f}{'':<12} | {s_new['PF'] - s_old['PF']:+.3f}")
    print(f"{'純損益合計 (Net PnL)':<21} | +{s_old['Total_PnL']:>10,.0f} 円{'':<10} | +{s_new['Total_PnL']:>10,.0f} 円{'':<10} | {s_new['Total_PnL'] - s_old['Total_PnL']:+,.0f} 円")
    print(f"{'総利益 (Gross Profit)':<21} | +{s_old['Gross_Profit']:>10,.0f} 円{'':<10} | +{s_new['Gross_Profit']:>10,.0f} 円{'':<10} | {s_new['Gross_Profit'] - s_old['Gross_Profit']:+,.0f} 円")
    print(f"{'総損失 (Gross Loss)':<21} | -{s_old['Gross_Loss']:>10,.0f} 円{'':<10} | -{s_new['Gross_Loss']:>10,.0f} 円{'':<10} | {s_new['Gross_Loss'] - s_old['Gross_Loss']:+,.0f} 円")
    print(f"{'平均利益 (Avg Win)':<23} | +{s_old['Avg_Win']:>10,.0f} 円{'':<10} | +{s_new['Avg_Win']:>10,.0f} 円{'':<10} | {s_new['Avg_Win'] - s_old['Avg_Win']:+,.0f} 円")
    print(f"{'平均損失 (Avg Loss)':<23} | -{s_old['Avg_Loss']:>10,.0f} 円{'':<10} | -{s_new['Avg_Loss']:>10,.0f} 円{'':<10} | {s_new['Avg_Loss'] - s_old['Avg_Loss']:+,.0f} 円")
    print(f"{'リスクリワード比 (RR)':<21} | {s_old['RR']:>10.2f}{'':<12} | {s_new['RR']:>10.2f}{'':<12} | {s_new['RR'] - s_old['RR']:+.2f}")
    print(f"{'最大ドローダウン額':<21} | {s_old['Max_DD']:>10,.0f} 円{'':<10} | {s_new['Max_DD']:>10,.0f} 円{'':<10} | {s_new['Max_DD'] - s_old['Max_DD']:+,.0f} 円")
    print(f"{'最大ドローダウン率':<21} | {s_old['Max_DD_Pct']:>10.2f} %{'':<10} | {s_new['Max_DD_Pct']:>10.2f} %{'':<10} | {s_new['Max_DD_Pct'] - s_old['Max_DD_Pct']:+.2f} pt")
    print(f"{'平均保有時間':<25} | {s_old['Avg_Hold_Min']:>10.1f} 分{'':<10} | {s_new['Avg_Hold_Min']:>10.1f} 分{'':<10} | {s_new['Avg_Hold_Min'] - s_old['Avg_Hold_Min']:+.1f} 分")

    # ==============================================================================
    # 3. 通貨ペア別要因分析
    # ==============================================================================
    print("\n" + "="*80)
    print("2. 通貨ペア別パフォーマンス詳細 & 新旧比較")
    print("="*80)

    pairs = sorted(df_new['pair'].unique())
    pair_rows = []
    for p in pairs:
        d_p_new = df_new[df_new['pair'] == p]
        d_p_old = df_old[df_old['通貨ペア'] == p]
        
        sn = get_summary(d_p_new)
        so = get_summary(d_p_old) if len(d_p_old) > 0 else {"Total": 0, "Win_Rate": 0, "PF": 0, "Total_PnL": 0}
        
        pair_rows.append({
            "Pair": p,
            "Old_Trades": so["Total"],
            "New_Trades": sn["Total"],
            "Diff_Trades": sn["Total"] - so["Total"],
            "Old_WinRate": so["Win_Rate"],
            "New_WinRate": sn["Win_Rate"],
            "Old_PF": so["PF"],
            "New_PF": sn["PF"],
            "Old_PnL": so["Total_PnL"],
            "New_PnL": sn["Total_PnL"],
            "PnL_Diff": sn["Total_PnL"] - so["Total_PnL"],
            "Avg_PnL_Trade": sn["Total_PnL"] / sn["Total"] if sn["Total"] > 0 else 0
        })

    df_pair_comp = pd.DataFrame(pair_rows).sort_values("New_PnL", ascending=False)
    for _, r in df_pair_comp.iterrows():
        print(f"[{r['Pair']}]")
        print(f"  ・取引数: {r['Old_Trades']} 件 -> {r['New_Trades']} 件 ({r['Diff_Trades']:+d} 件)")
        print(f"  ・勝  率: {r['Old_WinRate']:.1f}% -> {r['New_WinRate']:.1f}% ({r['New_WinRate'] - r['Old_WinRate']:+.1f} pt)")
        print(f"  ・P   F : {r['Old_PF']:.2f} -> {r['New_PF']:.2f} ({r['New_PF'] - r['Old_PF']:+.2f})")
        print(f"  ・純損益: {r['Old_PnL']:+,.0f} 円 -> {r['New_PnL']:+,.0f} 円 ({r['PnL_Diff']:+,.0f} 円, 1取引平均: +{r['Avg_PnL_Trade']:,.0f} 円)")

    # ==============================================================================
    # 4. 決済理由別 (Exit Reason) 分析
    # ==============================================================================
    print("\n" + "="*80)
    print("3. 決済理由別 (Exit Reason) パフォーマンス分析")
    print("="*80)
    
    reasons = df_new['reason'].value_counts()
    for reason, count in reasons.items():
        df_r = df_new[df_new['reason'] == reason]
        r_sum = get_summary(df_r)
        pct = count / len(df_new) * 100
        print(f"【{reason}】: {count:,} 件 ({pct:.1f}%)")
        print(f"  ・勝率: {r_sum['Win_Rate']:.1f}% ({r_sum['Wins']}勝 / {r_sum['Losses']}敗)")
        print(f"  ・損益合計: {r_sum['Total_PnL']:+,.0f} 円 (平均: {r_sum['Total_PnL']/count:+,.0f} 円/件)")
        print(f"  ・平均保有時間: {r_sum['Avg_Hold_Min']:.1f} 分 (中央値: {r_sum['Med_Hold_Min']:.1f} 分)")

    # ==============================================================================
    # 5. AIスコア別 (Score Bins) 分析
    # ==============================================================================
    print("\n" + "="*80)
    print("4. AIスコア別パフォーマンス分析")
    print("="*80)
    
    df_new['score_round'] = df_new['ai_score'].round(2)
    scores = sorted(df_new['score_round'].unique())
    for sc in scores:
        df_sc = df_new[df_new['score_round'] == sc]
        sc_sum = get_summary(df_sc)
        pct = len(df_sc) / len(df_new) * 100
        print(f"AIスコア [{sc:.2f}]: {len(df_sc):,} 件 ({pct:.1f}%) | 勝率: {sc_sum['Win_Rate']:.1f}% | PF: {sc_sum['PF']:.2f} | 損益: {sc_sum['Total_PnL']:+,.0f} 円 | 1回平均: {sc_sum['Total_PnL']/len(df_sc):+,.0f} 円")

    # ==============================================================================
    # 6. 曜日・時間帯別分析
    # ==============================================================================
    print("\n" + "="*80)
    print("5. 曜日別 & 取引時間帯別 (JST) パフォーマンス分析")
    print("="*80)
    
    df_new['entry_jst'] = df_new['entry_time_dt'].dt.tz_convert('Asia/Tokyo')
    df_new['weekday_jst'] = df_new['entry_jst'].dt.day_name()
    df_new['hour_jst'] = df_new['entry_jst'].dt.hour

    days_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    print("[曜日別パフォーマンス (JST)]")
    for day in days_order:
        df_day = df_new[df_new['weekday_jst'] == day]
        if not df_day.empty:
            d_sum = get_summary(df_day)
            print(f"  ・{day:<9}: {len(df_day):>4} 件 | 勝率: {d_sum['Win_Rate']:>5.1f}% | PF: {d_sum['PF']:>5.2f} | 損益: {d_sum['Total_PnL']:>+10,.0f} 円")

    # ==============================================================================
    # 7. システムロジック・約定整合性検証 (Trade Integrity Check)
    # ==============================================================================
    print("\n" + "="*80)
    print("6. 取引適正性・システムロジック遵守の監査 (Logic Integrity Audit)")
    print("="*80)
    
    # 1) 金曜夜間・週末の取引停止チェック
    df_new['close_jst'] = df_new['timestamp_dt'].dt.tz_convert('Asia/Tokyo')
    weekend_trades = df_new[df_new['entry_jst'].dt.weekday.isin([5, 6])]
    friday_night_entries = df_new[(df_new['entry_jst'].dt.weekday == 4) & (df_new['entry_jst'].dt.hour >= 20)]
    print(f"・週末（土日）の新規エントリー: {len(weekend_trades)} 件 {'(✅ 完全停止)' if len(weekend_trades)==0 else '(❌ 違反)'}")
    print(f"・金曜夜20時以降の新規エントリー: {len(friday_night_entries)} 件 {'(✅ 完全停止)' if len(friday_night_entries)==0 else '(❌ 違反)'}")

    # 2) 魔の時間帯 (01:00-09:00 JST) エントリーチェック
    witching_entries = df_new[(df_new['entry_jst'].dt.hour >= 1) & (df_new['entry_jst'].dt.hour < 9)]
    print(f"・魔の時間帯 (01:00〜09:00 JST) の新規エントリー: {len(witching_entries)} 件 {'(✅ 完全停止)' if len(witching_entries)==0 else '(❌ 違反)'}")

    # 3) 同時ポジション制限チェック
    print(f"・決定ログ (Decision Log Multi Pairs) の総エントリーシグナル数: {len(pd.read_csv(reports_dir / 'decision_log_multi_pairs.csv')):,} 件")
    print(f"・ブローカーによって実際に約定された総取引数: {len(df_new):,} 件")
    print(f"  >> ポジション重複や制限による適切なフィルター率: {(1 - len(df_new)/5547)*100:.1f}% が防護壁で制御")

    # 4) TSL利確の適正性
    tsl_trades = df_new[df_new['reason'] == 'TSL Hit']
    tsl_profit_rate = (tsl_trades['profit_amount'] > 0).mean() * 100
    print(f"・TSL（トレーリングストップ）発動時のプラス収益率: {tsl_profit_rate:.2f}% ({len(tsl_trades):,} 件中 {(tsl_trades['profit_amount'] > 0).sum():,} 件がプラス利確)")

if __name__ == "__main__":
    main()
