import pandas as pd
import glob

def analyze_signal_exits():
    # 仮想トレードのログをすべて読み込む
    log_files = glob.glob("data/reports/virtual_trade_log_*.csv")
    if not log_files:
        log_files = glob.glob("logs/virtual_trade_log_*.csv")
        
    if not log_files:
        print("エラー: virtual_trade_log CSVファイルが見つかりません。")
        return

    dfs = []
    for f in log_files:
        try:
            df = pd.read_csv(f)
            dfs.append(df)
        except Exception:
            pass
            
    if not dfs: return
    
    trades = pd.concat(dfs, ignore_index=True)
    
    print("\n" + "="*60)
    print(" 🌊 深堀り解析：Signal Exit（波の衰え決済）の真実")
    print("="*60)
    
    # Signal Exitのみを抽出 (reasonに 'Signal Exit' が含まれるもの)
    sig_exits = trades[trades['reason'].astype(str).str.contains('Signal Exit', case=False, na=False)]
    
    if sig_exits.empty:
        print("Signal Exitのデータがありません。")
        return
        
    print(f"\n【1. Signal Exit 全体サマリー】")
    print(f" 発動回数: {len(sig_exits)} 回 (全体の {len(sig_exits)/len(trades)*100:.1f}%)")
    print(f" 平均損益: {sig_exits['profit_amount'].mean():.0f} JPY")
    print(f" 勝率(プラス決済の割合): {(sig_exits['profit_amount'] > 0).mean()*100:.1f}%")

    # 通貨ペア別のSignal Exit成績
    print("\n【2. 通貨ペア別 Signal Exit の成績】")
    pair_stats = sig_exits.groupby('pair', observed=False).agg(
        Count=('profit_amount', 'count'),
        Win_Rate=('profit_amount', lambda x: (x > 0).mean() * 100),
        Avg_Profit=('profit_amount', 'mean')
    )
    for index, row in pair_stats.iterrows():
        print(f" {index: <8} | 発動: {int(row['Count']):>3}回 | 勝率: {row['Win_Rate']:>5.1f}% | 平均損益: {row['Avg_Profit']:>5.0f} JPY")

    # GBP_JPY の 負けトレード分析 (ADXとの相関)
    print("\n【3. GBP_JPY 負けトレードの環境分析 (ADX欠如の影響)】")
    gbp_losses = trades[(trades['pair'] == 'GBP_JPY') & (trades['profit_amount'] < 0)]
    
    if not gbp_losses.empty and 'ADX_M15' in gbp_losses.columns:
        # ADXが記録されている場合、ADXの値で層別化
        low_adx = gbp_losses[gbp_losses['ADX_M15'] < 25]
        high_adx = gbp_losses[gbp_losses['ADX_M15'] >= 25]
        
        print(f" GBP_JPY 負けトレード総数: {len(gbp_losses)}回")
        print(f" ┣ ADX < 25 (レンジ相場での負け): {len(low_adx)}回 ({len(low_adx)/len(gbp_losses)*100:.1f}%)")
        print(f" ┗ ADX >= 25 (トレンド相場での負け): {len(high_adx)}回 ({len(high_adx)/len(gbp_losses)*100:.1f}%)")
        print(" ※レンジ相場での負けが多い場合、ADXフィルターの復活が極めて有効です。")
    else:
        print(" ADXのデータ列が見つからないため、環境分析をスキップします。")

    # 損益帯別の分布
    print("\n【4. Signal Exit 損益帯分布（どのように逃げているか）】")
    bins = [-99999, -2000, -500, 0, 500, 2000, 99999]
    labels = ['大損(<-2k)', '中損(-2k～-500)', '微損(-500～0)', '微益(0～500)', '中益(500～2k)', '大益(>2k)']
    sig_exits_copy = sig_exits.copy()
    sig_exits_copy['profit_bin'] = pd.cut(sig_exits_copy['profit_amount'], bins=bins, labels=labels)
    dist = sig_exits_copy['profit_bin'].value_counts(sort=False)
    for label, count in dist.items():
        print(f" {label: <15}: {count:>3} 回 ({count/len(sig_exits)*100:.1f}%)")
        
    print("="*60 + "\n")

if __name__ == "__main__":
    analyze_signal_exits()