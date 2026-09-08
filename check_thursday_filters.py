import json
import os
import glob

print("=== 1. config.json の木曜フィルター設定確認 ===")
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    time_filters = config.get('system', {}).get('time_filters', {})
    th_filters = time_filters.get('thursday_filters', {})
    print(json.dumps(th_filters, indent=2))
    
    if not th_filters:
        print("🚨エラー: thursday_filters が config.json (system -> time_filters 内) に見つかりません。")
except Exception as e:
    print(f"config.json 読み込みエラー: {e}")

print("\n=== 2. エラーログの捜索 ===")
error_found = False
log_files = glob.glob('logs/*.log')
for file in log_files:
    try:
        with open(file, 'r', encoding='utf-8') as f:
            for line in f:
                if "Time filter parsing error" in line or "function_error in generate_signal" in line:
                    print(line.strip())
                    error_found = True
    except Exception:
        pass

if not error_found:
    print("✅ 関連するエラーログは検出されませんでした（設定読み込みミスの可能性大）。")
