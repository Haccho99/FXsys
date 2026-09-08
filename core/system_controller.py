"""
core/system_controller.py
"""
from __future__ import annotations
import shutil
from typing import Optional, TYPE_CHECKING
from pathlib import Path
from datetime import datetime
import json

# --- ▼▼▼【重要】ここでのインポートは型ヒントと純粋なモジュールのみにする ---
from core.logger import get_logger, log_error
from core.trade import get_executor
from core.notify import notify_all

if TYPE_CHECKING:
    from core.config_manager import ConfigManager

# --- ▼▼▼【重要】モジュールレベルのcfgとloggerのインスタンス化を完全に削除 ---

class SystemController:
    def __init__(self, cfg_manager: "ConfigManager", redis_client):
        self.cfg = cfg_manager
        self.redis_client = redis_client
        self.status_key = "system:trading_status"
        # --- ▼▼▼【重要】ロガーをクラスのインスタンス変数として、渡されたcfgを使って正しく初期化 ---
        self.logger = get_logger(self.cfg, "system_controller")
        self.logger.info("SystemController initialized.")

    async def get_trading_status(self) -> str:
        status = await self.redis_client.get(self.status_key)
        return status if status else "RUNNING"

    async def pause_trading(self) -> str:
        await self.redis_client.set(self.status_key, "PAUSED")
        self.logger.warning("System trading has been PAUSED by command.")
        response_message = "システムは **一時停止** しました。新規エントリーは行われません。"
        await notify_all(response_message, alert_type="trading_status_change")
        return response_message

    async def resume_trading(self) -> str:
        await self.redis_client.set(self.status_key, "RUNNING")
        self.logger.warning("System trading has been RESUMED by command.")
        response_message = "システムは **稼働中** に戻りました。通常の取引を再開します。"
        await notify_all(response_message, alert_type="trading_status_change")
        return response_message

    async def handle_summary_command(self, ctx, pair: str):
        try:
            await ctx.send(f"通貨ペア`{pair}`のパフォーマンスサマリーを集計します... 📊")
            log_file = self.cfg.get_sync("system.summary_log_file")
            if not log_file:
                await ctx.send("エラー: 分析対象のログファイルがconfig.jsonで設定されていません。")
                return
            
            import sys
            from pathlib import Path
            
            project_root = Path(__file__).resolve().parent.parent
            if str(project_root) not in sys.path:
                sys.path.append(str(project_root))
            
            try:
                # ルートディレクトリにある場合 (静的解析エラーを抑制)
                from trade_analyzer import get_performance_summary  # type: ignore
            except ImportError:
                # 万が一、coreフォルダ内に存在する場合のフォールバック (静的解析エラーを抑制)
                from core.trade_analyzer import get_performance_summary  # type: ignore

            summary_dict = await get_performance_summary(pair, log_file)

            if "error" in summary_dict:
                msg = f"エラー: {summary_dict['error']}"
            elif summary_dict.get("Trade Count", 0) == 0:
                msg = f"**{summary_dict['Pair']}** の取引履歴はありません。"
            else:
                msg = (f"--- **パフォーマンスサマリー ({summary_dict['Pair']})** ---\n"
                       f"対象ログ: `{Path(log_file).name}`\n"
                       f"```\n"
                       f"総取引回数 : {summary_dict['Trade Count']}\n"
                       f"勝ちトレード: {summary_dict.get('Wins', 'N/A')}\n"
                       f"負けトレード: {summary_dict.get('Losses', 'N/A')}\n"
                       f"勝率         : {summary_dict.get('Win Rate (%)', 'N/A')} %\n"
                       f"総損益 (Pips): {summary_dict.get('Total PnL', 'N/A')}\n"
                       f"```")
            await ctx.send(msg)
        except Exception as e:
            self.logger.error(f"Error handling summary command for {pair}: {e}", exc_info=True)
            await ctx.send(f"サマリー生成中に予期せぬエラーが発生しました。")

    async def handle_status_command(self) -> str:
        try:
            trading_status = await self.get_trading_status()
            redis_ping = await self.redis_client.ping()
            redis_status = "✅ 接続中" if redis_ping else "❌ 切断"
            executor = await get_executor()
            open_trades = executor.get_all_open_trades()
            position_count = sum(len(trades) for trades in open_trades.values()) if open_trades else 0
            positions_str = "なし"
            if position_count > 0:
                pos_list = [f"- {pair} {trade['side']} ({trade['current_units']} units)" for pair, trades in open_trades.items() for trade in trades]
                positions_str = "\n".join(pos_list)

            message = (f"--- **システムステータスレポート** ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---\n"
                       f"```\n"
                       f"取引ループ状態 : {trading_status}\n"
                       f"Redis接続     : {redis_status}\n"
                       f"保有ポジション数: {position_count}\n"
                       f"```\n")
            if position_count > 0:
                message += f"**保有ポジション詳細:**\n```\n{positions_str}\n```\n"
            return message
        except Exception as e:
            self.logger.error("Error handling status command", exc_info=True)
            return f"ステータス取得中に予期せぬエラーが発生しました: {e}"

    async def handle_approve_proposal_command(self, proposal_id: str) -> str:
        self.logger.info(f"Received approval for proposal: {proposal_id}")
        proposal_key = f"ai_proposal:{proposal_id}"
        try:
            proposal_json = await self.redis_client.get(proposal_key)
            if not proposal_json: return f"❌ 提案ID '{proposal_id}' が見つからないか、期限切れです。"
            
            proposal_data = json.loads(proposal_json)
            backup_path = self.cfg.backup_config()
            if not backup_path: return "❌ 設定ファイルのバックアップに失敗しました。"
            
            changes_applied = []
            for pair, strats in proposal_data.items():
                for strategy, params in strats.items():
                    for key, value in params.items():
                        full_key = f"best_params.{pair}.{strategy}.{key}"
                        if self.cfg.update_value(full_key, value):
                            changes_applied.append(f"- `{full_key}`: `{value}`")
                        else:
                            return f"❌ 提案適用中にエラー: キー `{full_key}` の更新に失敗。"
            
            self.cfg.save_config()
            await self.redis_client.delete(proposal_key)
            changes_str = "\n".join(changes_applied)
            return (f"✅ 提案 **{proposal_id}** が適用されました。\n"
                    f"**バックアップ:** `{backup_path.name}`\n"
                    f"**変更内容:**\n"
                    f"```\n{changes_str}\n```")
        except Exception as e:
            await log_error(self.logger, "handle_approve_proposal_command", error=e, proposal_id=proposal_id)
            return f"❌ 提案の適用中に予期せぬエラーが発生しました。"

    async def handle_reject_proposal_command(self, proposal_id: str) -> str:
        self.logger.info(f"Received rejection for proposal: {proposal_id}")
        proposal_key = f"ai_proposal:{proposal_id}"
        try:
            deleted_count = await self.redis_client.delete(proposal_key)
            return f"🗑️ 提案 **{proposal_id}** は却下・削除されました。" if deleted_count > 0 else f"❓ 提案ID '{proposal_id}' は見つかりませんでした。"
        except Exception as e:
            await log_error(self.logger, "handle_reject_proposal_command", error=e, proposal_id=proposal_id)
            return f"❌ 提案の却下中にエラーが発生しました。"

    def _backup_py_file(self, file_path: Path) -> Optional[Path]:
        try:
            if not file_path.exists():
                self.logger.error(f"Backup target not found: {file_path}")
                return None
            backup_dir = self.cfg.project_root / self.cfg.get_sync("system.backup_dir", "backup")
            backup_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            backup_path = backup_dir / f"{file_path.name}.{timestamp}.bak"
            shutil.copy(file_path, backup_path)
            self.logger.info(f"Created backup for {file_path.name} at {backup_path.name}")
            return backup_path
        except Exception:
            self.logger.error(f"Failed to backup {file_path.name}", exc_info=True)
            return None

    async def handle_approve_features_command(self, proposal_id: str) -> str:
        self.logger.info(f"Received approval for feature proposal: {proposal_id}")
        proposal_key = f"ai_proposal:features:{proposal_id}"
        try:
            proposal_json = await self.redis_client.get(proposal_key)
            if not proposal_json: return f"❌ 特徴量提案ID '{proposal_id}' が見つからないか、期限切れです。"
            
            proposal_data = json.loads(proposal_json)
            new_features = proposal_data.get("recommended_features")
            if not isinstance(new_features, list): return f"❌ 提案データ形式が不正です。"

            config_path = self.cfg.project_root / "core" / "ai_config.py"
            backup_path = self._backup_py_file(config_path)
            if not backup_path: return "❌ `ai_config.py`のバックアップに失敗しました。"

            try:
                lines = config_path.read_text("utf-8").splitlines()
                with open(config_path, "w", encoding="utf-8") as f:
                    in_features_block = False
                    for line in lines:
                        if line.strip().startswith("SELECTED_FEATURES"):
                            in_features_block = True
                            formatted_list = ",\n".join([f'    "{feature}"' for feature in new_features])
                            f.write(f"SELECTED_FEATURES = [\n{formatted_list},\n]\n")
                        elif in_features_block and "]" in line:
                            in_features_block = False
                            continue
                        elif not in_features_block:
                            f.write(f"{line}\n")
                self.logger.info(f"`ai_config.py` updated for proposal {proposal_id}.")
            except Exception as e:
                await log_error(self.logger, "update_ai_config_file", e, proposal_id=proposal_id)
                return f"❌ `ai_config.py`のファイル書き込み中にエラーが発生しました。"

            await self.redis_client.delete(proposal_key)
            features_str = ", ".join(f"`{f}`" for f in new_features)
            return (f"✅ 特徴量提案 **{proposal_id}** が適用されました。\n"
                    f"**バックアップ:** `{backup_path.name}`\n"
                    f"**新しい特徴量:** {features_str}")
        except Exception as e:
            await log_error(self.logger, "handle_approve_features_command", e, proposal_id=proposal_id)
            return f"❌ 特徴量提案の適用中に予期せぬエラーが発生しました。"

    async def handle_reject_features_command(self, proposal_id: str) -> str:
        self.logger.info(f"Received rejection for feature proposal: {proposal_id}")
        proposal_key = f"ai_proposal:features:{proposal_id}"
        try:
            deleted_count = await self.redis_client.delete(proposal_key)
            return f"🗑️ 特徴量提案 **{proposal_id}** は却下・削除されました。" if deleted_count > 0 else f"❓ 特徴量提案ID '{proposal_id}' は見つかりませんでした。"
        except Exception as e:
            await log_error(self.logger, "handle_reject_features_command", error=e, proposal_id=proposal_id)
            return f"❌ 提案の却下処理中にエラーが発生しました。"