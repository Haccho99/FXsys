import polars as pl
import pandas as pd
import asyncio
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import uuid

from core.config_manager import ConfigManager
from core.logger import get_logger, log_error

cfg = ConfigManager(Path(__file__).resolve().parent.parent)
logger = get_logger(cfg, "analysis_engine")

async def generate_trade_report_async(pair: str, strategy: str = "trend") -> str:
    """バックテストを実行し、トレードレポート(Parquet)を生成する（本実装）"""
    try:
        from core.backtest.backtester import run_backtest
        from core.redis_client import create_redis_client
        redis = create_redis_client(cfg)
        
        output_dir = Path(cfg.get_sync("system.log_dir", "logs"))
        output_dir.mkdir(exist_ok=True)
        
        params_key = f"best_params.{pair}.{strategy}"
        params = cfg.get_sync(params_key, {})
        
        output_filename = f"features_master_{pair}_{strategy}.parquet"
        full_output_path = output_dir / output_filename
        
        # configから期間を動的に取得
        start_date = cfg.get_sync("backtest.start_date", "2020-01-01T00:00:00Z")
        end_date = cfg.get_sync("backtest.end_date", "2025-01-01T00:00:00Z")

        # バックテストの本実行
        await run_backtest(
            pair=pair, 
            strategy_name=strategy, 
            params=params, 
            start_date_str=start_date, 
            end_date_str=end_date, 
            output_log_filename=str(full_output_path), 
            redis=redis
        )
        
        if redis:
            await redis.aclose()
            
        return f"レポート生成完了: {output_filename}"
    except Exception as e:
        await log_error(logger, "generate_trade_report_async", e)
        return f"エラー: {e}"

async def analyze_trades_async(pair: str, strategy: str = "trend") -> str:
    """生成されたレポート(Parquet)を読み込み、WFA分析を含む人間向けのテキストレポートを作成する"""
    try:
        output_dir = Path(cfg.get_sync("system.log_dir", "logs"))
        files = list(output_dir.glob(f"features_master_{pair}_{strategy}.parquet"))
        if not files:
            files = list(output_dir.glob("features_master_*.parquet"))
            
        if not files:
            return "分析対象の取引データ(Parquet)が見つかりません。先にダッシュボードからレポート生成を実行してください。"
            
        df = pl.read_parquet(files[0])
        if df.is_empty():
            return "取引データが空です。"

        # 損益カラムの特定
        pnl_col = "PnL" if "PnL" in df.columns else "Return Pips" if "Return Pips" in df.columns else "profit_amount" if "profit_amount" in df.columns else None
        if not pnl_col:
            return "損益カラム(PnL または Return Pips)が見つかりません。"

        trade_count = len(df)
        wins = df.filter(pl.col(pnl_col) > 0).height
        win_rate = (wins / trade_count) * 100 if trade_count > 0 else 0
        total_pnl = df[pnl_col].sum()

        report = f"### {pair} ({strategy}) 総合パフォーマンス\n"
        report += f"- **総取引回数**: {trade_count}\n"
        report += f"- **勝率**: {win_rate:.2f}%\n"
        report += f"- **総損益**: {total_pnl:,.2f}\n"

        # 決済理由の分析
        reason_col = "Exit Reason Detail" if "Exit Reason Detail" in df.columns else "reason" if "reason" in df.columns else None
        if reason_col:
            reason_summary = df.group_by(reason_col).agg([
                pl.len().alias("回数"),
                pl.col(pnl_col).sum().alias("合計PnL"),
                pl.col(pnl_col).mean().round(2).alias("平均PnL")
            ]).sort("回数", descending=True).to_pandas().to_string(index=False)
            report += f"\n#### 決済理由の内訳\n```text\n{reason_summary}\n```\n"

        # WFA (ウォークごとの安定性) 分析
        if "Walk Number" in df.columns:
            walk_summary = df.group_by("Walk Number").agg([
                pl.col(pnl_col).sum().alias("Total PnL"),
                pl.len().alias("Trade Count"),
                (pl.col(pnl_col) > 0).mean().round(4).alias("Win Rate")
            ]).sort("Walk Number")
            
            profitable_walks = walk_summary.filter(pl.col("Total PnL") > 0).height
            total_walks = walk_summary.height
            consistency = (profitable_walks / total_walks) * 100 if total_walks > 0 else 0
            
            report += f"\n#### ウォークごとの安定性 (Walk-Forward Analysis)\n"
            report += f"- **収益性ウォークの割合**: {consistency:.2f}%\n"
            report += f"```text\n{walk_summary.to_pandas().to_string(index=False)}\n```\n"

        return report
    except Exception as e:
        await log_error(logger, "analyze_trades_async", e)
        return f"分析エラー: {e}"

async def run_ai_strategy_analysis_async(report_text: str) -> dict:
    """人間向けのレポートを元に、生成AI連携を行い改善提案を出させる（本実装スケルトン）"""
    try:
        # プロンプトの構築（LangChain等に渡すための準備）
        prompt = f"""
        あなたは、天才的なFXストラテジーアナリストです。
        以下のウォークフォワード分析レポートを詳細に分析し、プロの視点から「戦略の要約」と「具体的な改善提案」をJSON形式で出力してください。
        ---
        {report_text}
        ---
        """
        
        # ＝＝＝ AI API通信のプレースホルダー ＝＝＝
        # 実際にはここに LLM（Gemini API等）を呼び出すコードが入ります。
        # 例: response = await llm_client.generate(prompt)
        await asyncio.sleep(1) # API通信のオーバーヘッドを想定
        
        # 今回は、受け取った report_text の中身を動的に解析し、擬似的にAIの回答を生成します
        target_param = "sl_mult"
        reason = "レポートから損切りの多さが読み取れます。市場のボラティリティに適応するため、SLの設定値幅を見直すべきです。"
        
        if "Wave Ended" in report_text or "Wave Ended - Long" in report_text:
            target_param = "macd_histogram_threshold"
            reason = "決済理由としてWave Ended（早期撤退）が多発しています。MACDヒストグラムの閾値を調整し、ノイズによる途中下車を防ぐべきです。"
            
        ai_response = {
            "summary": "分析完了。勝率とプロフィットファクターは基準を満たしていますが、一部の決済ロジックに利益を取り逃がす原因が潜んでいます。",
            "recommendation": {
                "parameter_to_tune": target_param,
                "reason": reason
            }
        }
        # ＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝＝
        
        # AIの提案履歴をCSVに永続化（ダッシュボードで読み込むため）
        output_dir = Path(cfg.get_sync("system.log_dir", "logs"))
        proposal_file = output_dir / f"ai_proposals_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
        
        proposal_id = f"AI-{uuid.uuid4().hex[:8]}"
        param = ai_response["recommendation"]["parameter_to_tune"]
        ai_reason = ai_response["recommendation"]["reason"]
        
        line = f"{proposal_id},ALL,\"{param} (Tune recommended)\",N/A,pending,\"{ai_reason}\"\n"
        
        if not proposal_file.exists():
            with open(proposal_file, "w", encoding="utf-8") as f:
                f.write("proposal_id,pair,parameters,sharpe_ratio,status,reason\n")
        with open(proposal_file, "a", encoding="utf-8") as f:
            f.write(line)

        return ai_response
    except Exception as e:
        await log_error(logger, "run_ai_strategy_analysis_async", e)
        return {"summary": f"AI分析中にエラーが発生しました: {e}", "recommendation": {}}