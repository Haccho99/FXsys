import glob
import os

print("=== 1. WFA結果ファイルの捜索 ===")
wfa_files = glob.glob('data/*wfa*.csv') + glob.glob('logs/*wfa*.csv') + glob.glob('data/*wfa*.parquet') + glob.glob('*wfa*.csv')
for f in wfa_files:
    print(f)

print("\n=== 2. AIレポート/提案履歴の捜索 ===")
ai_reports = glob.glob('data/ai_*.csv') + glob.glob('logs/ai_*.csv') + glob.glob('data/*report*.md') + glob.glob('data/*report*.txt')
for f in ai_reports:
    print(f)

print("\n=== 3. 分析ツールのパス確認 ===")
tools = ['generate_trade_report.py', 'trade_analyzer.py', 'wfa_reporter.py', 'ai_strategy_analyzer.py']
for tool in tools:
    found = False
    for root, dirs, files in os.walk('.'):
        if tool in files:
            print(f"発見: {os.path.join(root, tool)}")
            found = True
            break
    if not found:
        print(f"未発見: {tool}")
