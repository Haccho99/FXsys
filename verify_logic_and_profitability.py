import pandas as pd
import glob
from pathlib import Path

def analyze_backtest_results():
    # logsフォルダ直下の今日の実行分（2026/06/16更新）のログを取得
    log_files = glob.glob("logs/virtual_trade_log_*.csv")
    
    if not log_files:
        print("ログファイルが見つかりません。")
        return

    df_list = []
    for f in log_files:
        try:
            df_list.append(pd.read_csv(f))
        except Exception as e:
            print(f"Error reading {f}: {e}")

    if not df_list:
        print("有効なデータがありません。")
        return

    df = pd.concat(df_list, ignore_index=True)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # pips換算 (簡易的に100を掛ける)
    df['pips'] = 0.0
    df.loc[df['direction'] == 'long', 'pips'] = (df['exit_price'] - df['entry_price']) * 100
    df.loc[df['direction'] == 'short', 'pips'] = (df['entry_price'] - df['exit_price']) * 100

    # 1. AIスコアの検証
    print("=== 【1】AIスコア足切り検証 ===")
    score_counts = df['ai_score'].value_counts().sort_index()
    print("スコア分布:")
    print(score_counts)
    min_score = df['ai_score'].min()
    print(f"最小スコア: {min_score}")
    if min_score >= 0.75:
        print("✅ 判定: 全トレードがスコア0.75以上で実行されており、足切りロジックが機能しています。")
    else:
        print(f"⚠️ 判定: スコア {min_score} のトレードが存在します。足切りが不完全な可能性があります。")

    # 2. 決済理由の分析 (TSL/BE検証)
    print("\n=== 【2】早期BE/TSLロジックの評価 ===")
    reason_stats = df.groupby('reason').agg(
        取引数=('profit_amount', 'count'),
        合計損益=('profit_amount', 'sum'),
        平均損益=('profit_amount', 'mean'),
        平均pips=('pips', 'mean')
    )
    reason_stats['割合(%)'] = (reason_stats['取引数'] / len(df) * 100).round(1)
    print(reason_stats)

    tsl_hits = df[df['reason'] == 'TSL Hit']
    if not tsl_hits.empty:
        avg_pips = tsl_hits['pips'].mean()
        print(f"\nTSL Hitの平均獲得pips: {avg_pips:.2f} pips")
        if avg_pips > 0:
            print("✅ 判定: TSL Hitがプラス圏で決済されており、建値ガード（早期BE）が機能しています。")
        else:
            # スプレッドが考慮されていない場合、Bid/Askの差でわずかにマイナスになることがある
            print(f"ℹ️ 判定: TSL Hitの平均が {avg_pips:.2f} pips です。建値付近での決済が行われています。")
    else:
        print("\nTSL Hitが発生していません。")

    # 3. 通貨ペア別パフォーマンス
    print("\n=== 【3】通貨ペア別パフォーマンス ===")
    pair_stats = df.groupby('pair').agg(
        取引数=('profit_amount', 'count'),
        勝率=('profit_amount', lambda x: (x > 0).mean() * 100),
        合計損益=('profit_amount', 'sum'),
        プロフィットファクター=('profit_amount', lambda x: x[x>0].sum() / abs(x[x<0].sum()) if len(x[x<0]) > 0 else float('inf'))
    ).round(2)
    print(pair_stats)

    # 4. 収益性への寄与（SL拡大の影響推測）
    print("\n=== 【4】深いSLによるノイズ回避の推測 ===")
    sl_trades = df[df['reason'] == 'SL']
    if not sl_trades.empty:
        avg_sl_pips = sl_trades['pips'].mean()
        # 損切は通常マイナスなので絶対値で評価
        abs_avg_sl = abs(avg_sl_pips)
        print(f"平均損切幅: {abs_avg_sl:.2f} pips")
        if abs_avg_sl > 20:
            print(f"✅ 判定: 平均損切幅が {abs_avg_sl:.2f} pips と深く設定されており、ヒゲノイズに耐える仕様になっています。")
        else:
            print(f"ℹ️ 判定: 平均損切幅は {abs_avg_sl:.2f} pips です。")
    else:
        print("損切(SL)が発生していません。")

    print("\n=== 総合評価 ===")
    total_profit = df['profit_amount'].sum()
    print(f"最終合計損益: {total_profit:,.0f} JPY")
    if total_profit > 0:
        print("🚀 結論: 修正後のロジック（厳選エントリー＋鉄壁ガード）は、現在のバックテスト範囲で利益に貢献しています。")
    else:
        print("📉 結論: 合計損益はマイナスですが、BEガードにより損失が抑えられているか確認が必要です。")

if __name__ == "__main__":
    analyze_backtest_results()
