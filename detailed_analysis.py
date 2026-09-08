import pandas as pd
import glob
from pathlib import Path
import numpy as np

print("="*60)
print(" 🕵️‍♂️ Gemini CLI: 取引ログ詳細解析")
print("="*60)

log_dir = Path("logs")
trade_log_files = list(log_dir.glob("virtual_trade_log_*.csv"))

if trade_log_files:
    print(f"[データ集計] {len(trade_log_files)} 個の取引ログを統合解析します...")
    combined_trade_df = pd.concat([pd.read_csv(f) for f in trade_log_files])
    
    # 時間をパース (entry_timeを使用)
    combined_trade_df['entry_datetime'] = pd.to_datetime(combined_trade_df['entry_time'])
    combined_trade_df['hour'] = combined_trade_df['entry_datetime'].dt.hour
    
    # ① 魔の時間帯ガードのチェック (01:00 - 08:59)
    forbidden_trades = combined_trade_df[(combined_trade_df['hour'] >= 1) & (combined_trade_df['hour'] <= 8)]
    print(f"\n🛡️ 【検証】防衛壁（時間帯ガード）")
    print(f"魔の時間帯(01:00-08:59)のエントリー数: {len(forbidden_trades)} 件 / 全 {len(combined_trade_df)} 件")
    
    if len(forbidden_trades) == 0:
        print("  => 完璧です。早朝のエントリーは完全に排除されています。")
    else:
        print(f"  => ⚠️ 警告: {len(forbidden_trades)} 件のエントリーが禁止時間帯に漏れています。")
        print(forbidden_trades[['entry_time', 'pair', 'direction', 'profit_amount']].head(10).to_string(index=False))

    # ② パフォーマンス詳細
    win_trades = combined_trade_df[combined_trade_df['profit_amount'] > 0]
    loss_trades = combined_trade_df[combined_trade_df['profit_amount'] <= 0]
    
    total_trades = len(combined_trade_df)
    win_rate = (len(win_trades) / total_trades * 100) if total_trades > 0 else 0
    total_profit = combined_trade_df['profit_amount'].sum()
    
    def calc_pips(row):
        mult = 100.0 if 'JPY' in str(row.get('pair', '')) else 10000.0
        diff = row['exit_price'] - row['entry_price']
        return diff * mult if row['direction'] == 'long' else -diff * mult
    
    combined_trade_df['pips'] = combined_trade_df.apply(calc_pips, axis=1)
    
    avg_tp = combined_trade_df[combined_trade_df['profit_amount'] > 0]['pips'].mean()
    avg_sl = combined_trade_df[combined_trade_df['profit_amount'] <= 0]['pips'].mean()
    
    print("\n📊 【集計結果】")
    print(f"総取引回数 : {total_trades} 回")
    print(f"勝率       : {win_rate:.1f} %")
    print(f"純利益     : {total_profit:,.0f} JPY")
    print(f"平均利確幅 : {avg_tp:.1f} pips")
    print(f"平均損切幅 : {avg_sl:.1f} pips")
    print(f"実質RR比   : {abs(avg_tp/avg_sl) if avg_sl != 0 and not pd.isna(avg_sl) else 0:.2f}")

    # ③ 通貨ペア別の成績
    print("\n💹 【通貨ペア別成績】")
    pair_stats = combined_trade_df.groupby('pair').agg(
        trades=('pair', 'count'),
        profit=('profit_amount', 'sum'),
        avg_pips=('pips', 'mean')
    )
    print(pair_stats.to_string())

    # ④ SL下限のチェック
    print("\n🛡️ 【検証】SL下限ガード (10.0 pips)")
    # 損失トレードのpipsの絶対値を確認
    sl_pips_abs = abs(combined_trade_df[combined_trade_df['profit_amount'] <= 0]['pips'])
    min_sl = sl_pips_abs.min()
    print(f"最小損切幅: {min_sl:.2f} pips")
    if min_sl >= 9.9: # 誤差考慮
        print("  => 正常: すべての損切りが10pips以上確保されています。")
    else:
        print("  => ⚠️ 警告: 10pipsを下回る損切りが検出されました。")

else:
    print("取引ログが見つかりません。")
