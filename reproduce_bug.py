import asyncio
import sys
import os

# coreディレクトリをパスに追加
sys.path.append(os.path.abspath('.'))

async def test_emulation():
    try:
        from core.risk import tp_sl, _calculate_trade_parameters
        
        pair = "USD_JPY"
        side = "long"
        entry_price = 157.1235
        dummy_atr_pips = 15.0 # 標準的なATR値
        strategy = "macd_wave_mtf"
        
        print("=== 内部パラメータの生データ検証 ===")
        # 1. 内部で計算されるpips幅を直接のぞき見る
        sl_pips, tp_pips, mult = _calculate_trade_parameters(pair, dummy_atr_pips)
        print(f"  内部の想定SL値幅  : {sl_pips} pips")
        print(f"  内部の想定TP値幅  : {tp_pips} pips")
        
        # 2. 実際に virtual_trade.py に渡される最終価格を再現計算
        # ユーザーの監査スクリプトでは tp_sl(pair=pair, ...) となっているのでそれに合わせる
        # もし引数が古いままならエラーになるはず
        try:
            tp, sl, trail, use_gslo = await tp_sl(
                pair=pair, side=side, price=entry_price, 
                atr_pips=dummy_atr_pips, strategy_name=strategy
            )
        except TypeError:
            # 旧引数（pairなし）の場合のフォールバック
            tp, sl, trail, use_gslo = await tp_sl(
                side=side, price=entry_price, 
                atr_pips=dummy_atr_pips, strategy_name=strategy
            )
        
        print("\n=== 最終決済ラインの再現出力 ===")
        print(f"  [エントリー建値] : {entry_price:.4f}")
        print(f"  [システム計算SL] : {sl:.4f}  (理想は 157.0235 付近)")
        print(f"  [システム計算TP] : {tp:.4f}  (理想は 157.2435 付近)")
        
        # 差分のpips換算
        actual_sl_pips = (entry_price - sl) * 100
        actual_tp_pips = (tp - entry_price) * 100
        print(f"\n  実際のSL距離: {actual_sl_pips:.1f} pips")
        print(f"  実際のTP距離: {actual_tp_pips:.1f} pips")
        
    except Exception as e:
        import traceback
        print(f"エラーが発生しました: {e}")
        traceback.print_exc()

# 実行
if __name__ == "__main__":
    asyncio.run(test_emulation())
