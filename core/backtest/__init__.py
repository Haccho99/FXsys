"""
Initializes the backtest sub-package.
"""
from pathlib import Path

# coreパッケージのルートで定義された、公式のcfgインスタンスをインポートする
from core import cfg

# このファイルはサブパッケージの目印として機能するため、
# これ以上のコードは不要な場合が多いです。
# 必要に応じて、このパッケージから外部に公開したい関数やクラスを
# ここでインポートすることもできます。
# 例: from .backtester import run_backtest