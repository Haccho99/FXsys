import pandas as pd
import glob
from pathlib import Path

def evaluate_adx_potential():
    decision_csv = Path("data/reports/decision_log_multi_pairs.csv")
    df_decisions = pd.read_csv(decision_csv)
    df_decisions['entry_time_clean'] = pd.to_datetime(df_decisions['Time']).dt.strftime('%Y-%m-%d %H:%M')
    
    # decision_logはGBP_JPY単独で回したため、GBP_JPYのみのはず
    df_decisions = df_decisions[df_decisions['Pair'] == 'GBP_JPY']

    log_files = glob.glob("logs/virtual_trade_log_*.csv")
    dfs = []
    for f in log_files:
        try:
            dfs.append(pd.read_csv(f))
        except:
            continue
            
    df_trades = pd.concat(dfs, ignore_index=True)
    df_trades['entry_time_clean'] = pd.to_datetime(df_trades['entry_time']).dt.strftime('%Y-%m-%d %H:%M')
    
    df_merged = pd.merge(
        df_decisions, 
        df_trades, 
        how='inner', 
        left_on=['entry_time_clean', 'Pair', 'Direction'],
        right_on=['entry_time_clean', 'pair', 'direction']
    )
    df_merged = df_merged.drop_duplicates(subset=['entry_time_clean', 'pair', 'direction'])
    
    total_trades = len(df_merged)
    print(f"実際の実行トレード数: {total_trades}")
    
    # もしADXが20以上だったら？
    df_adx = df_merged[df_merged['ADX_M15'] >= 20.0]
    
    adx_trades = len(df_adx)
    adx_wins = len(df_adx[df_adx['profit_amount'] > 0])
    adx_loss = len(df_adx[df_adx['profit_amount'] <= 0])
    adx_win_rate = adx_wins / adx_trades * 100 if adx_trades > 0 else 0
    adx_profit = df_adx['profit_amount'].sum()
    adx_avg = df_adx['profit_amount'].mean() if adx_trades > 0 else 0
    
    print("\n【ADXフィルターが有効だった場合のシミュレーション (ADX >= 20)】")
    print(f"取引数: {adx_trades} 回 (元の{total_trades}回から除外)")
    print(f"勝率: {adx_win_rate:.2f}% ({adx_wins}勝 / {adx_loss}敗)")
    print(f"合計損益: {adx_profit:,.0f} JPY")
    print(f"平均損益: {adx_avg:,.0f} JPY")
    
    # ADXが25以上だったら？
    df_adx25 = df_merged[df_merged['ADX_M15'] >= 25.0]
    adx25_trades = len(df_adx25)
    adx25_wins = len(df_adx25[df_adx25['profit_amount'] > 0])
    adx25_win_rate = adx25_wins / adx25_trades * 100 if adx25_trades > 0 else 0
    adx25_profit = df_adx25['profit_amount'].sum()
    adx25_avg = df_adx25['profit_amount'].mean() if adx25_trades > 0 else 0
    print("\n【さらに厳しいADX >= 25 の場合】")
    print(f"取引数: {adx25_trades} 回")
    print(f"勝率: {adx25_win_rate:.2f}%")
    print(f"合計損益: {adx25_profit:,.0f} JPY")
    print(f"平均損益: {adx25_avg:,.0f} JPY")

if __name__ == "__main__":
    evaluate_adx_potential()
