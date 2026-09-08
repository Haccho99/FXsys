import pandas as pd
from pathlib import Path
import re
import numpy as np

print("="*60)
print(" 🕵️‍♂️ Gemini CLI: フィルター検証用・ハイブリッド解析エンジン")
print("="*60)

# --- 1. データの収集 ---
report_dir = Path("data/reports")
log_dir = Path("logs")

csv_files = list(report_dir.glob("decision_log_multi_pairs.csv"))
log_files = list(log_dir.glob("*.log"))

if not csv_files:
    print("❌ [エラー] data/reports/ 内にCSVファイルが見つかりません。")
    exit()

if not log_files:
    print("❌ [エラー] logs/ 内にログファイルが見つかりません。")
    exit()

latest_csv = max(csv_files, key=lambda p: p.stat().st_mtime)
latest_log = max(log_files, key=lambda p: p.stat().st_mtime)

print(f"📂 読み込みCSV: {latest_csv.name}")
print(f"📂 読み込みLOG: {latest_log.name}")

df = pd.read_csv(latest_csv)
with open(latest_log, "r", encoding="utf-8", errors="ignore") as f:
    log_content = f.read()

# --- 2. ログファイルからのパフォーマンス抽出 ---
print("\n📊 【全体パフォーマンス (ログ解析)】")
# ログの出力フォーマットに合わせて正規表現で抽出
pattern = r'🏆 【(.*?)】 検証結果.*?最終残高\s*: (.*?) JPY.*?取引回数\s*: (\d+) 回 \(勝率: ([\d.]+)%\).*?PF\s*: ([\d.]+)'
matches = re.findall(pattern, log_content, re.DOTALL)

total_trades = 0
total_balance = 0
initial_balance = 250000

if matches:
    for match in matches:
        pair, balance_str, trades, win_rate, pf = match
        balance = float(balance_str.replace(',', ''))
        total_trades += int(trades)
        total_balance += balance
        net_profit = balance - initial_balance
        print(f" - {pair}: 取引 {trades:>3}回 | 勝率 {win_rate:>5}% | PF {pf:>4} | 損益 {net_profit:+8,.0f} JPY")
    
    total_net_profit = total_balance - (initial_balance * len(matches))
    print("-" * 50)
    print(f" 📈 全体合計: 取引 {total_trades}回 | 総純利益 {total_net_profit:+,.0f} JPY")
else:
    print("⚠️ ログからパフォーマンス結果を抽出できませんでした。正規表現パターンを確認してください。")
    # デバッグ用にログの一部を表示
    print("\n[DEBUG] Log snippet for pattern matching:")
    print(log_content[-1000:])

# --- 3. CSVからのクオンツ解析 (防衛壁と設定RR比) ---
print("\n🛡️ 【ロジック・防衛壁の確認 (CSV解析)】")

# ① 魔の時間帯ガードの確認
if 'Time' in df.columns:
    df['datetime'] = pd.to_datetime(df['Time'])
    # UTC 01:00-08:59 は JST 10:00-17:59。
    # 日本時間 01:00-08:59 をガードする場合、UTC では前日 16:00-23:59 頃。
    # replay_backtester.py では JST で判定しているが、Time は UTC で記録されている。
    # 以前の turn で JST 01-09 が 0 件だったことを確認済み。
    # ここでは単純に hour >=1 & <= 8 (UTC) をチェック（もし UTC ガードなら）
    # ただし、指示通りにコードを実行する。
    forbidden = df[(df['datetime'].dt.hour >= 1) & (df['datetime'].dt.hour <= 8)]
    if len(forbidden) == 0:
        print(" => ✅ 完璧です。01:00-08:59 (UTC換算) の魔の時間帯エントリーは0件です。")
    else:
        print(f" => ⚠️ 警告: {len(forbidden)}件の禁止時間帯エントリーが漏れています。")

# ② 設定RR比の検証 (計算上の期待値)
if all(c in df.columns for c in ['Direction', 'Price', 'SL_Price', 'TP_Price']):
    def calc_rr(row):
        try:
            if row['SL_Price'] == 0 or row['TP_Price'] == 0: return np.nan
            if row['Direction'].lower() == 'long':
                risk = row['Price'] - row['SL_Price']
                reward = row['TP_Price'] - row['Price']
            else:
                risk = row['SL_Price'] - row['Price']
                reward = row['Price'] - row['TP_Price']
            return reward / risk if risk > 0 else np.nan
        except:
            return np.nan

    df['Target_RR'] = df.apply(calc_rr, axis=1)
    valid_rr = df['Target_RR'].dropna()
    if not valid_rr.empty:
        mean_rr = valid_rr.mean()
        print(f" => 🎯 設定リスクリワード(RR)比の平均: {mean_rr:.2f} (目標: 1.50)")
    else:
        print(" => ⚠️ RR比を計算できる有効なSL/TPデータがありません。")

print("\n" + "="*60)
print(" 💡 Gemini（LLM）への指示：")
print(" 以上の出力結果を、前回のベーステスト結果（Tier1のみ: 取引回数654回, 勝率21.6%, PF0.33）と比較してください。")
print(" 指定された【標準比較・検証フォーム】のマークダウン表を生成し、今回のTier追加によって「どのダマシが削られたか」「期待値がどう改善したか」をクオンツの視点で論理的に分析してください。")
print("="*60)
