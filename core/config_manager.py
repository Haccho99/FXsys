"""config_manager.py (v7 - Backup and Update Methods)"""
# 修正日: 2025-08-02 (Gemini)
# 修正内容:
# - backup_config: 設定ファイルをタイムスタンプ付きでバックアップする機能を追加。
# - update_value: メモリ上の設定値をドット区切りキーで安全に更新する機能を追加。
# - 既存の堅牢な読み取り・保存ロジックは維持。

import json
import logging
from pathlib import Path
from typing import Any, Dict
import shutil
from datetime import datetime

logger = logging.getLogger(__name__)

class ConfigManager:
    def __init__(self, project_root: Path | str):
        self.project_root = Path(project_root)
        self.config_path = self.project_root / "config.json"
        self.config: Dict[str, Any] = {}
        self.load_config()

    def load_config(self):
        try:
            with self.config_path.open("r", encoding="utf-8") as f:
                self.config = json.load(f)
            logger.info(f"Config loaded from: {self.config_path}")
        except FileNotFoundError:
            logger.error(f"Config file not found at: {self.config_path}")
            self.config = {}
        except Exception as e:
            logger.error(f"Failed to load config file: {e}", exc_info=True)
            self.config = {}

    def get_sync(self, key_path: str, default: Any = None) -> Any:
        try:
            keys = key_path.split('.')
            value = self.config
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default
        
    def backup_config(self) -> Path | None:
        """
        現在の設定ファイルをタイムスタンプ付きでバックアップする。
        """
        try:
            backup_dir = self.project_root / "config_backups"
            backup_dir.mkdir(exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file_name = f"config_{timestamp}.json"
            backup_path = backup_dir / backup_file_name
            
            shutil.copy2(self.config_path, backup_path)
            
            logger.info(f"Configuration successfully backed up to {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"Failed to backup config file: {e}", exc_info=True)
            return None
        
    def update_value(self, key_string: str, new_value: Any):
        """
        ドット記法のキー文字列を使って、ネストした設定値を更新する。
        例: update_value("best_params.USD_JPY.trend.ema_fast_span", 10)
        """
        try:
            keys = key_string.split('.')
            d = self.config
            for key in keys[:-1]:
                d = d[key]
            d[keys[-1]] = new_value
            logger.info(f"Config value updated: {key_string} = {new_value}")
            return True
        except KeyError:
            logger.error(f"Invalid key string for config update: {key_string}")
            return False
        except Exception as e:
            logger.error(f"Failed to update config value for key '{key_string}'", exc_info=True)
            return False

    def save_config(self):
        """
        現在のメモリ上の設定をconfig.jsonファイルに保存する。
        保存前に自動的にバックアップを作成し、誤操作時の復旧を可能にする。
        """
        try:
            # 🚨 追加: 上書き保存する前に必ず現在の状態をバックアップ
            self.backup_config()

            # 最新のディスク内容をロードしてマージ（他プロセスや手動で追加されたキーの消失を防ぐ）
            if self.config_path.exists():
                try:
                    with self.config_path.open("r", encoding="utf-8") as f:
                        disk_config = json.load(f)
                    for k, v in disk_config.items():
                        if k not in self.config:
                            self.config[k] = v
                except Exception as load_err:
                    logger.warning(f"Failed to merge disk config before saving: {load_err}")

            with self.config_path.open("w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
            logger.info(f"設定が正常に {self.config_path} に保存されました。")
            return True
        except Exception as e:
            logger.error(f"設定ファイルの保存に失敗しました: {e}", exc_info=True)
            return False