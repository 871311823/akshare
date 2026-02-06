#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""分析单只股票是否符合选股条件"""

import akshare as ak
from datetime import datetime, timedelta

symbol = '002714'
name = '牧原股份'

import time

# 获取周线数据（带重试）
end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - timedelta(days=3*365)).strftime('%Y%m%d')

df = None
# 尝试东方财富
for retry in range(3):
    try:
        df = ak.stock_zh_a_hist(symbol=symbol, period='weekly', start_date=start_date, end_date=end_date, adjust='qfq')
        if df is not None and len(df) > 0:
            print('数据源: 东方财富')
            break
    except Exception as e:
        print(f'东方财富重试 {retry+1}/3')
        time.sleep(1)

# 如果失败，尝试腾讯
if df is None or len(df) == 0:
    print('尝试腾讯数据源...')
    try:
        import pandas as pd
        prefix = 'sz' if symbol.startswith(('0', '3')) else 'sh'
        df_daily = ak.stock_zh_a_hist_tx(symbol=f'{prefix}{symbol}', start_date=start_date, end_date=end_date, adjust='qfq')
        if df_daily is not None and len(df_daily) > 100:
            df_daily['date'] = pd.to_datetime(df_daily['date'])
            df_daily.set_index('date', inplace=True)
            df_w = df_daily.resample('W').agg({'open':'first', 'high':'max', 'low':'min', 'close':'last', 'amount':'sum'}).dropna().reset_index()
            # 重命名为中文
            df = df_w.rename(columns={'date':'日期', 'open':'开盘', 'high':'最高', 'low':'最低', 'close':'收盘', 'amount':'成交量'})
            print('数据源: 腾讯')
    except Exception as e:
        print(f'腾讯失败: {e}')

if df is None or len(df) == 0:
    print('所有数据源均失败')
    exit(1)
    
print(f'数据条数: {len(df)} 周')

# 计算MACD
close = df['收盘']
ema12 = close.ewm(span=12, adjust=False).mean()
ema26 = close.ewm(span=26, adjust=False).mean()
df['DIF'] = ema12 - ema26
df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
df['MACD'] = 2 * (df['DIF'] - df['DEA'])
df['MA55'] = close.rolling(window=55).mean()

# 当前数据
cur = df.iloc[-1]
prev = df.iloc[-2]
max_dea_100w = df.tail(100)['DEA'].max()

close_price = cur['收盘']
ma55 = cur['MA55']
dea = cur['DEA']
dif = cur['DIF']
macd = cur['MACD']
prev_macd = prev['MACD']

deviation = (close_price - ma55) / ma55

print(f'''
{'='*60}
{symbol} {name} 周线分析
{'='*60}

【当前指标】
  收盘价: {close_price:.2f}
  MA55: {ma55:.2f}
  MA55偏离度: {deviation*100:.2f}%
  DIF: {dif:.4f}
  DEA: {dea:.4f}
  100周DEA最高: {max_dea_100w:.4f}
  DEA回落比: {(1-dea/max_dea_100w)*100:.1f}%
  MACD柱: {macd:.4f}
  上周MACD柱: {prev_macd:.4f}

【条件检查】
''')

# 三种模式参数 (以牧原股份002714为锚点)
modes = {
    'strict': {'name':'严格', 'ma_min':0, 'ma_max':0.08, 'hist_dea':0.3, 'pullback':0.3, 'dea_min':0, 'dea_max':0.5},
    'default': {'name':'默认', 'ma_min':-0.05, 'ma_max':0.10, 'hist_dea':0.1, 'pullback':0.5, 'dea_min':0, 'dea_max':1.0},
    'loose': {'name':'宽松', 'ma_min':-0.10, 'ma_max':0.20, 'hist_dea':0.05, 'pullback':0.7, 'dea_min':-0.2, 'dea_max':1.5},
}

for mode, p in modes.items():
    c1 = p['ma_min'] <= deviation < p['ma_max']
    c2 = max_dea_100w > p['hist_dea']
    c3 = dea < p['pullback'] * max_dea_100w
    c4 = p['dea_min'] < dea < p['dea_max']
    
    all_pass = c1 and c2 and c3 and c4
    
    status = '✅ 符合' if all_pass else '❌ 不符合'
    print(f"  【{p['name']}模式】 {status}")
    print(f"    1.均线支撑({p['ma_min']*100:.0f}%~{p['ma_max']*100:.0f}%): {deviation*100:.2f}% -> {'✓' if c1 else '✗'}")
    print(f"    2.历史高度(>{p['hist_dea']}): {max_dea_100w:.4f} -> {'✓' if c2 else '✗'}")
    print(f"    3.充分回调(<{p['pullback']*100:.0f}%): {dea/max_dea_100w*100:.1f}% -> {'✓' if c3 else '✗'}")
    print(f"    4.零轴企稳({p['dea_min']}~{p['dea_max']}): {dea:.4f} -> {'✓' if c4 else '✗'}")
    print()

# 趋势信号
if dif > dea:
    signal = '金叉 ✨'
elif macd > prev_macd and macd < 0:
    signal = '绿柱缩短 📈'
else:
    signal = '待金叉 ⏳'
print(f'【趋势信号】: {signal}')
