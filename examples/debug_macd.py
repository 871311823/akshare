#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""调试脚本：检查月线MACD条件"""

import akshare as ak
import pandas as pd
from datetime import datetime, timedelta


def calculate_macd(df, fast=12, slow=26, signal=9):
    close = df['收盘']
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    df['DIF'] = ema_fast - ema_slow
    df['DEA'] = df['DIF'].ewm(span=signal, adjust=False).mean()
    df['MACD柱'] = 2 * (df['DIF'] - df['DEA'])
    return df


# 测试几只股票
symbols = ['000001', '600519', '000858', '300750', '002594']
end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - timedelta(days=48*30)).strftime('%Y%m%d')

for symbol in symbols:
    try:
        df = ak.stock_zh_a_hist(symbol=symbol, period='monthly', start_date=start_date, end_date=end_date, adjust='qfq')
        if df is None or len(df) < 36:
            print(f'{symbol}: 数据不足')
            continue
            
        df = calculate_macd(df)
        df_36m = df.tail(36)
        current = df.iloc[-1]
        prev = df.iloc[-2]
        
        max_dea = df_36m['DEA'].max()
        cur_dea = current['DEA']
        cur_dif = current['DIF']
        prev_dif = prev['DIF']
        
        print(f"\n{'='*50}")
        print(f"股票: {symbol}")
        print(f"36个月DEA最高值: {max_dea:.4f}")
        print(f"当前DEA: {cur_dea:.4f}")
        print(f"当前DIF: {cur_dif:.4f}")
        print(f"上月DIF: {prev_dif:.4f}")
        print(f"DEA回落比: {cur_dea/max_dea*100:.1f}%")
        print(f"\n条件判断:")
        print(f"  1. 历史DEA > 0.1: {max_dea > 0.1} (值={max_dea:.4f})")
        print(f"  2. 当前DEA < 50%历史最高: {cur_dea < 0.5 * max_dea} (阈值={0.5*max_dea:.4f})")
        print(f"  3. DEA > 0: {cur_dea > 0}")
        print(f"  4. DIF > 0: {cur_dif > 0}")
        print(f"  5. DEA < 0.5: {cur_dea < 0.5}")
        print(f"  6. DIF > DEA (金叉): {cur_dif > cur_dea}")
        print(f"  7. DIF拐头向上: {cur_dif > prev_dif}")
        
        # 统计通过了几个条件
        conditions = [
            max_dea > 0.1,
            cur_dea < 0.5 * max_dea,
            cur_dea > 0,
            cur_dif > 0,
            cur_dea < 0.5,
            cur_dif > cur_dea,
            cur_dif > prev_dif
        ]
        print(f"\n通过条件数: {sum(conditions)}/7")
        
    except Exception as e:
        print(f'{symbol}: 错误 - {e}')
