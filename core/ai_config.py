# ai_config.py(v1)
"""AIモデルが使用する特徴量を一元管理するための設定ファイル"""

# AIの学習と予測に使用する特徴量のリスト
SELECTED_FEATURES = [
    "atr",          # 市場のボラティリティ（重要）
    "bb_width",     # 市場のボラティリティ（重要）
    "volume",       # 市場の活況度（重要）
    "adx",          # トレンドの強さ
    "rsi",          # 相対的な勢い
    "macd_hist",    # モメンタムの変化
    "score",        # 基本的なシグナルスコア
    "hour",         # 時間帯のアノマリー
    "day_of_week",  # 曜日のアノマリー
    #'score', 'atr', 'rsi', 'bb_width', 'adx', 'di_plus', 
    #'di_minus', 'h1_ema_score', 'orderbook_score', 'ema_fast_span', 
    #'ema_slow_span', 'bb_period', 'bb_std', 'bb_squeeze_pctl', 'macd', 
    #'macd_signal', 'macd_hist', 'hour', 'day_of_week', 'volatility', 'roc'
    # 'lot_ratio',
]
