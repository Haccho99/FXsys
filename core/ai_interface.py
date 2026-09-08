"""ai_interface.py (v6 - Modernized for Core)
# 修正日: 2025-07-08 (Gemini)
# 修正内容:
# - build_features関数で、入力がPolarsかPandasのDataFrameであるかを判断し、
#   適切に処理するように修正。AttributeErrorを解消。
"""
from __future__ import annotations
import pandas as pd
import polars as pl
import lightgbm as lgb
import xgboost as xgb
import joblib
from pathlib import Path
from sklearn.model_selection import train_test_split, StratifiedKFold
from sklearn.metrics import roc_auc_score, accuracy_score
import optuna
import json
from datetime import datetime, timezone, timedelta
import numpy as np
from typing import Dict, Tuple, Optional, List
from lightgbm import LGBMClassifier

# ▼▼▼ 修正: coreパッケージの作法に統一 ▼▼▼
from core import cfg
from core.logger import get_logger, append_csv, log_error
from core.notify import notify_all
from .ai_config import SELECTED_FEATURES

logger = get_logger(cfg, "ai_interface")
_model_cache = {}
MODEL_PATH = cfg.project_root / cfg.get_sync("ai.lgbm_model_path", "model.joblib")
XGB_MODEL_PATH = cfg.project_root / cfg.get_sync("ai.xgb_model_path", "xgb_model.joblib")
STATUS_PATH = cfg.project_root / cfg.get_sync("system.log_dir", "logs") / "ai_status.json"
WINDOW = 200

def build_features(df: pl.DataFrame | pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series] | tuple[None, None, None, None]:
    """Build features and labels for model training."""
    try:
        if isinstance(df, pl.DataFrame):
            df_pd = df.to_pandas()
        elif isinstance(df, pd.DataFrame):
            df_pd = df
        else:
            raise TypeError(f"Unsupported DataFrame type: {type(df)}")

        excluded_cols = ['timestamp', 'pair', 'side', 'units', 'pnl', 'label', 'score', 'lot_ratio']
        features = [col for col in df_pd.columns if col not in excluded_cols]
        
        if not features:
            logger.warning("No feature columns found in the training data.")
            return None, None, None, None

        X = df_pd[features].copy()
        y = df_pd["label"]

        for col in X.columns:
            X[col] = pd.to_numeric(X[col], errors='coerce')
        X = X.fillna(0)

        return train_test_split(X, y, test_size=0.3, random_state=42, stratify=y if y.nunique() > 1 else None)
    except Exception as e:
        logger.exception(f"Error in build_features: {e}")
        return None, None, None, None

def tune_hyperparameters(X_train: pd.DataFrame, y_train: pd.Series) -> dict:
    """
    OptunaとPruningを用いて、LightGBMのハイパーパラメータを効率的に探索する。
    """
    def objective(trial: optuna.Trial) -> float:
        params = {
            "objective": "binary", "metric": "auc", "verbosity": -1, "boosting_type": "gbdt",
            "random_state": 42, "n_jobs": -1,
            "n_estimators": trial.suggest_int("n_estimators", 200, 2000, step=100),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2),
            "num_leaves": trial.suggest_int("num_leaves", 20, 500),
            "max_depth": trial.suggest_int("max_depth", 4, 15),
            "lambda_l1": trial.suggest_float("lambda_l1", 1e-8, 10.0, log=True),
            "lambda_l2": trial.suggest_float("lambda_l2", 1e-8, 10.0, log=True),
        }
        model = lgb.LGBMClassifier(**params)
        
        pruning_callback = optuna.integration.LightGBMPruningCallback(trial, "auc")
        
        skf = StratifiedKFold(n_splits=3, shuffle=True, random_state=42)
        scores = []
        for train_idx, valid_idx in skf.split(X_train, y_train):
            X_train_fold, X_valid_fold = X_train.iloc[train_idx], X_train.iloc[valid_idx]
            y_train_fold, y_valid_fold = y_train.iloc[train_idx], y_train.iloc[valid_idx]
            
            # ▼▼▼【ここからが修正箇所】▼▼▼
            # 検証データにラベルが1種類しかない場合、AUCが計算できずエラーになるのを防ぐ
            if y_valid_fold.nunique() < 2:
                continue # このフォールドの評価をスキップ
            # ▲▲▲【修正はここまで】▲▲▲

            model.fit(X_train_fold, y_train_fold,
                      eval_set=[(X_valid_fold, y_valid_fold)],
                      eval_metric="auc",
                      callbacks=[pruning_callback],
                      )
            preds = model.predict_proba(X_valid_fold)[:, 1]
            scores.append(roc_auc_score(y_valid_fold, preds))
        
        # スコアが一つも計算できなかった場合は、最低スコアを返す
        if not scores:
            return 0.0
            
        return np.mean(scores)

    pruner = optuna.pruners.MedianPruner(n_warmup_steps=5)
    study = optuna.create_study(direction="maximize", pruner=pruner)
    study.optimize(objective, n_trials=50, catch=(Exception,))
    return study.best_params

def optimize_and_train_model(df: pl.DataFrame, model_save_path: str | Path, selected_features: list[str]) -> tuple[lgb.LGBMClassifier | None, dict]:
    """
    特徴量データフレームからAIモデルを訓練し、指定されたパスに保存する。
    """
    try:
        final_df = df.select(["label"] + selected_features)
        X_train, X_test, y_train, y_test = build_features(final_df)
        if X_train is None or X_train.empty: 
            return None, {}

        # Pruningが有効化されたチューニング関数を呼び出す
        best_params = tune_hyperparameters(X_train, y_train)
        
        model_params = {"objective": "binary", "metric": "auc", "random_state": 42, "n_jobs": -1, **best_params}
        lgb_model = lgb.LGBMClassifier(**model_params)
        lgb_model.fit(X_train, y_train, eval_set=[(X_test, y_test)], eval_metric="auc", callbacks=[lgb.early_stopping(10)])

        save_path = Path(model_save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(lgb_model, save_path)

        y_pred_proba = lgb_model.predict_proba(X_test)[:, 1]
        accuracy = accuracy_score(y_test, (y_pred_proba > 0.5).astype(int))
        roc_auc = roc_auc_score(y_test, y_pred_proba)
        
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": "lgbm",
            "accuracy": f"{accuracy:.3f}",
            "roc_auc": f"{roc_auc:.3f}",
            "params": json.dumps(best_params)
        }
        return lgb_model, log_data
    except Exception as e:
        logger.exception(f"Error in train_model: {e}")
        return None, {}

def load_model(strategy_name: str) -> Tuple[Optional[LGBMClassifier], Optional[List[str]]]:
    """
    モデルオブジェクトと、モデルが学習した特徴量のリストをタプルで返すように修正。
    """
    model_filename = f"model_{strategy_name}.joblib"
    model_path = cfg.project_root / model_filename
    
    if model_path.exists():
        try:
            logger.info(f"Loading AI model for strategy '{strategy_name}' from {model_path}")
            model = joblib.load(model_path)
            model_features = model.feature_name_
            return model, model_features
        except Exception as e:
            logger.critical(f"Failed to load model file from {model_path}", exc_info=True)
            return None, None
    else:
        logger.critical(f"AI model file for strategy '{strategy_name}' not found at {model_path}")
        return None, None

def predict_score(features: Dict[str, float], strategy_name: str) -> float:
    """
    load_modelが2つの値を返す仕様に合わせて修正。
    """
    model, model_features = load_model(strategy_name)
    if not model or not model_features:
        return 0.5

    try:
        # モデルが学習した特徴量リストに基づいて、入力データから必要な値を正しい順序で抽出
        feature_values = [features.get(f, 0.0) for f in model_features]
        df_features = pd.DataFrame([feature_values], columns=model_features)
        
        score = model.predict_proba(df_features)[0][1]
        return float(score)
    except Exception as e:
        logger.error(f"Error during AI score prediction for strategy '{strategy_name}'", exc_info=True)
        return 0.5

async def monitor_performance(trades: pd.DataFrame):
    if len(trades) < WINDOW: return
    
    recent = trades.tail(WINDOW)
    win_rate = (recent["profit"] > 0).mean()
    sharpe = recent["profit"].mean() / (recent["profit"].std() + 1e-9)
    
    status = {"use_adx": True, "use_di_plus_minus": True, "cooldown_until": None}
    if STATUS_PATH.exists():
        try: status.update(json.loads(STATUS_PATH.read_text()))
        except json.JSONDecodeError: pass

    if status.get("cooldown_until") and datetime.now(timezone.utc) < datetime.fromisoformat(status["cooldown_until"]):
        return

    alert = False
    auc = 0.0
    if "label" in recent.columns and all(k in recent.columns for k in ["delta", "atr", "rsi", "bb_width", "adx", "spread"]):
        model = load_model()
        if model:
            try:
                X = recent[["delta", "atr", "rsi", "bb_width", "adx", "spread"]]
                y = recent["label"]
                auc = roc_auc_score(y, model.predict_proba(X)[:, 1])
                # ▼▼▼ 修正: cfg.get を cfg.get_sync に修正 ▼▼▼
                auc_th = cfg.get_sync("ai.model_performance_threshold", 0.65)
                # ▲▲▲ 修正ここまで ▲▲▲
                if auc < auc_th or win_rate < 0.40 or sharpe < 0.0:
                    alert = True
            except Exception as e:
                logger.error("Error during performance monitoring calculation.", exc_info=True)

    if alert:
        alert_message = f"AI Model Performance Alert: Win rate: {win_rate:.2f}, Sharpe: {sharpe:.2f}, AUC: {auc:.2f}"
        await notify_all(message=alert_message, subject="【AI警告】", alert_type="ai_performance_alert")
        
        status["cooldown_until"] = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        STATUS_PATH.write_text(json.dumps(status, indent=2))
        
        await append_csv(
            cfg,
            f"ai_filter_log_{datetime.now(timezone.utc):%Y%m%d}.csv", 
            pd.DataFrame([{"timestamp": datetime.now(timezone.utc).isoformat(), "status": json.dumps(status)}])
        )