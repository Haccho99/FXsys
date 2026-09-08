# ai_strategy_analyzer.py (v2 - AI Analyst with Translation)
import sys
import json
import argparse
from pathlib import Path
import asyncio

# Discord通知など、プロジェクトのコア機能を利用するためにパスを追加
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from core.config_manager import ConfigManager
from core.notify import notify_discord

# --- Glossary Translation Logic ---

def load_glossary(glossary_path: Path) -> dict:
    """glossary.jsonを読み込み、パラメータ名と日本語名の対応辞書を作成する"""
    if not glossary_path.exists():
        print(f"Warning: Glossary file not found at {glossary_path}. Translation will be skipped.")
        return {}
    try:
        with open(glossary_path, 'r', encoding='utf-8') as f:
            glossary_data = json.load(f)
        # "term"をキー、"description"の最初の部分（例：「短期EMA」）を値とする辞書を作成
        return {item['term']: item['description'].split('説明:')[0].strip() for item in glossary_data}
    except Exception as e:
        print(f"Error loading glossary file: {e}")
        return {}

def translate_params_for_display(params: dict, glossary: dict) -> str:
    """AIが提案したパラメータ辞書を、用語集を使って日本語の文字列に変換する"""
    if not glossary:
        return json.dumps(params, indent=2, ensure_ascii=False)
    
    translated_lines = []
    for key, value in params.items():
        # glossaryにキーがあれば日本語名を、なければ元のキーをそのまま使う
        display_name = glossary.get(key, key)
        translated_lines.append(f"- **{display_name}**: `{value}`")
    return "\n".join(translated_lines)


# --- AI Analysis Logic ---

def build_prompt(report_text: str) -> str:
    """生成AIに与える詳細なプロンプトを構築する"""
    
    prompt = f"""
あなたは、天才的なFXストラテジーアナリストです。
以下のウォークフォワード分析レポートを詳細に分析し、プロの視点から「戦略の要約」と「具体的な改善提案」をJSON形式で出力してください。

---
# 分析レポート
{report_text}
---

# あなたのタスク
分析レポート、特に「決済理由の分析」セクションを重視して、以下の2つの項目を含むJSONオブジェクトを生成してください。

1.  **summary (string)**:
    戦略の全体的な健全性、強み、そして最も深刻な弱点を、80字以内で簡潔に要約してください。
    例: 「全体的に収益性は高いが、特定のウォークで損切りが多発しており、市場変動への適応力に課題が見られる。」

2.  **recommendation (object)**:
    分析に基づき、次回のパラメータ最適化（Optuna）で重点的に探索すべきパラメータとその理由を提案してください。
    提案は、`parameter_to_tune` (string) と `reason` (string) の2つのキーを持つオブジェクトにしてください。
    - `parameter_to_tune`: 改善に最も寄与すると考えられるパラメータ名を一つだけ挙げてください。（例: `sl_mult`）
    - `reason`: なぜそのパラメータが重要なのか、レポートのどの部分からそう判断したのかを具体的に記述してください。（例: 「全期間で損切りによる損失が最も大きく、特にWalk 3と5で顕著。SLの設定（sl_mult）が現在のボラティリティに合っていない可能性が高いため、この値を再探索し、損失を抑制すべき。」）

# 出力形式 (JSONのみ)
"""
    return prompt

async def request_ai_analysis(report_text: str) -> dict:
    """
    生成AIに分析を依頼し、結果をJSONで受け取る（この関数はシミュレーションです）。
    実際の運用では、ここにGemini APIなどを呼び出すコードを実装します。
    """
    prompt = build_prompt(report_text)
    
    # --- ここからAI API呼び出し（シミュレーション） ---
    # 実際には `response = await client.generate_content(prompt)` のようなコードになる
    print("\n--- AIへのプロンプト (シミュレーション) ---\n")
    print(prompt)
    print("\n----------------------------------------\n")
    
    # AIからの応答をシミュレートしたダミーデータ
    simulated_ai_response = {
        "summary": "プロフィットファクターは良好だが、損切り率の高さが収益を圧迫している。特に後半のウォークでの安定性低下が課題。",
        "recommendation": {
            "parameter_to_tune": "sl_mult",
            "reason": "決済理由分析から、全期間を通じて『Stop Loss』が最も多く、合計PnLも大きなマイナスとなっている。これは損切り設定がタイトすぎるか、市場の変動に適応できていないことを示唆しているため、sl_mult（損切り値幅の倍率）の探索範囲を見直すべき。"
        }
    }
    # --- AI API呼び出し（シミュレーション）ここまで ---

    return simulated_ai_response


# --- Main Execution ---

async def main():
    parser = argparse.ArgumentParser(
        description="Analyze WFA report with AI and notify results.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        '--glossary',
        type=str,
        default='glossary.json',
        help='Path to the glossary JSON file for parameter translation.'
    )
    args = parser.parse_args()

    # wfa_reporter.pyからの出力を標準入力で受け取る
    wfa_report_text = sys.stdin.read()
    
    if not wfa_report_text.strip():
        print("Error: Input report from stdin is empty.")
        return

    # AIに分析を依頼
    analysis_result = await request_ai_analysis(wfa_report_text)
    
    # 用語集をロード
    glossary = load_glossary(Path(args.glossary))

    # Discord通知メッセージを作成
    summary = analysis_result.get("summary", "要約の取得に失敗しました。")
    recommendation = analysis_result.get("recommendation", {})
    param_to_tune = recommendation.get("parameter_to_tune", "N/A")
    reason = recommendation.get("reason", "N/A")

    # パラメータ名を日本語に翻訳
    param_display_name = glossary.get(param_to_tune, param_to_tune)

    message = (
        f"🧠 **AIによる週次戦略レビュー** 🧠\n\n"
        f"直近のウォークフォワード分析が完了し、AIによる評価が行われました。\n\n"
        f"**【AIによる状況要約】**\n"
        f"> {summary}\n\n"
        f"**【AIからの改善提案】**\n"
        f"次回の最適化では、以下のパラメータを重点的に調整することを推奨します。\n\n"
        f"- **推奨パラメータ**: **{param_display_name}** (`{param_to_tune}`)\n"
        f"- **理由**: {reason}\n\n"
        f"この分析結果を基に、システムの自己進化サイクルが継続されます。"
    )
    
    # Discordに通知
    cfg = ConfigManager(project_root)
    await notify_discord(cfg, message, "ai_analysis")
    print("AI analysis report has been sent to Discord.")


if __name__ == "__main__":
    asyncio.run(main())