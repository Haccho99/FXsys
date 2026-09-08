import os

file_path = 'core/signal_module.py'

if not os.path.exists(file_path):
    print(f"🚨 エラー: {file_path} が見つかりません。")
else:
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    print("=== 1. 木曜フィルターの実装確認 ===")
    thursday_code_found = False
    for i, line in enumerate(lines):
        if 'current_weekday == 3' in line or 'thursday_filters' in line:
            print(f"Line {i+1}: {line.rstrip()}")
            thursday_code_found = True
    if not thursday_code_found:
        print("❌ 木曜日フィルターのコードが存在しません（実装漏れ・上書き失敗）。")

    print("\n=== 2. ml_scoreの抽出確認 ===")
    ml_score_found = False
    for i, line in enumerate(lines):
        if '"ml_score":' in line or "'ml_score':" in line:
            print(f"Line {i+1}: {line.rstrip()}")
            ml_score_found = True
    if not ml_score_found:
        print("❌ ml_score を返すコードが存在しません（実装漏れ・上書き失敗）。")
