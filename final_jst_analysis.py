import pandas as pd
import glob
from pathlib import Path
import numpy as np

print("="*60)
print(" 🕵️‍♂️ Gemini CLI: 大規模バックテスト最終解析 (JST対応版)")
print("="*60)

log_dir = Path("logs")
trade_log_files = list(log_dir.glob("virtual_trade_log_*.csv"))

if trade_log_files:
    print(f"[データ集計] {len(trade_log_files)} 個の取引ログを統合解析します...")
    combined_trade_df = pd.concat([pd.read_csv(f) for f in trade_log_files])
    
    # 重複排除 (エントリー時間と建値が同じなら同じトレードとみなす)
    combined_trade_df = combined_trade_df.drop_duplicates(subset=['entry_time', 'pair', 'direction', 'entry_price'])
    
    # 時間をパースしてJSTに変換
    combined_trade_df['entry_datetime'] = pd.to_datetime(combined_trade_df['entry_time'], utc=True)
    combined_trade_df['jst_datetime'] = combined_trade_df['entry_datetime'].dt.tz_convert('Asia/Tokyo')
    combined_trade_df['jst_hour'] = combined_trade_df['jst_datetime'].dt.hour
    
    print(f"解析対象期間: {combined_trade_df['jst_datetime'].min()} ～ {combined_trade_df['jst_datetime'].max()}")

    # ① 魔の時間帯ガードのチェック (JST 01:00 - 08:59)
    forbidden_trades = combined_trade_df[(combined_trade_df['jst_hour'] >= 1) & (combined_trade_df['jst_hour'] <= 8)]
    print(f"\n🛡️ 【検証1】防衛壁（時間帯ガード - JST 01:00-08:59）")
    print(f"禁止時間帯のエントリー数: {len(forbidden_trades)} 件 / 全 {len(combined_trade_df)} 件")
    
    if len(forbidden_trades) == 0:
        print("  => 🏆 完璧です！物理ゲートが100%機能し、魔の時間帯を完全に封鎖しました。")
    else:
        print(f"  => ⚠️ 警告: {len(forbidden_trades)} 件が漏れています。")
        print(forbidden_trades[['jst_datetime', 'pair', 'direction', 'profit_amount']].head(5).to_string(index=False))

    # ② パフォーマンス解析
    win_trades = combined_trade_df[combined_trade_df['profit_amount'] > 0]
    total_trades = len(combined_trade_df)
    win_rate = (len(win_trades) / total_trades * 100) if total_trades > 0 else 0
    total_profit = combined_trade_df['profit_amount'].sum()
    gross_profit = win_trades['profit_amount'].sum()
    gross_loss = abs(combined_trade_df[combined_trade_df['profit_amount'] <= 0]['profit_amount'].sum())
    pf = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
    
    def calc_pips(row):
        mult = 100.0 if 'JPY' in str(row.get('pair', '')) else 10000.0
        diff = row['exit_price'] - row['entry_price']
        return diff * mult if row['direction'] == 'long' else -diff * mult
    
    combined_trade_df['pips'] = combined_trade_df.apply(calc_pips, axis=1)
    avg_tp = combined_trade_df[combined_trade_df['profit_amount'] > 0]['pips'].mean()
    avg_sl = combined_trade_df[combined_trade_df['profit_amount'] <= 0]['pips'].mean()
    
    print("\n📊 【検証2】総合パフォーマンスレポート")
    print(f"総取引回数 : {total_trades} 回")
    print(f"勝率       : {win_rate:.1f} %")
    print(f"純利益     : {total_profit:,.0f} JPY")
    print(f"プロフィットファクター (PF) : {pf:.2f}")
    print(f"平均利確幅 : {avg_tp:.1f} pips")
    print(f"平均損切幅 : {avg_sl:.1f} pips")
    print(f"実質RR比   : {abs(avg_tp/avg_sl) if avg_sl != 0 and not pd.isna(avg_sl) else 0:.2f}")

    # ③ 通貨ペア別の成績
    print("\n💹 【検証3】通貨ペア別成績")
    pair_stats = combined_trade_df.groupby('pair').agg(
        取引数=('pair', 'count'),
        純利益=('profit_amount', 'sum'),
        勝率=('profit_amount', lambda x: (x > 0).mean() * 100),
        平均pips=('pips', 'mean')
    )
    print(pair_stats.to_string())

    # ④ SL下限のチェック
    print("\n🛡️ 【検証4】SL下限ガード (10.0 pips)")
    sl_pips_abs = abs(combined_trade_df[combined_trade_df['profit_amount'] <= 0]['pips'])
    if not sl_pips_abs.empty:
        min_sl = sl_pips_abs.min()
        print(f"最小損切幅: {min_sl:.2f} pips")
        if min_sl >= 9.9:
            print("  => 正常: すべての損切りが10pips以上確保されています。")
        else:
            print("  => ⚠️ 警告: 10pipsを下回る損切りが検出されました。")
    else:
        print("損切りトレードがありません。")

else:
    print("取引ログが見つかりません。")

print("\n" + "="*60)
print(" 🏁 大規模解析完了")
print("="*60)
