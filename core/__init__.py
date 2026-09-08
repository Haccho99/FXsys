# core/__init__.py
from pathlib import Path

# ▼▼▼ 修正: .parentを追加し、正しいプロジェクトルート(C:\debug_vbt)を取得 ▼▼▼
PROJECT_ROOT = Path(__file__).resolve().parent.parent

from .config_manager import ConfigManager
cfg = ConfigManager(PROJECT_ROOT)