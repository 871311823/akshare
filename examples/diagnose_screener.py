#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
诊断脚本：分析为什么股票未通过筛选
"""

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


def diagnose_stock(symbol: str, name: str = ""):
    """诊断单只股票为什么未通过筛选"""
    print(f"\n{'=' * 60}")
    print(f"诊断: {symbol} {name}")
    print(f"{'=' * 60}")
    
    # 获取数据（带重试和备用数据源）
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=10*365)).strftime("%Y%m%d")  # 10年数据
    
    df = None
    
    # 尝试东方财富（3次重试）
    import time
    for retry in range(3):
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="monthly",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"
            )
            if df is not None and len(df) > 0:
                break
        except Exception as e:
            if retry < 2:
                time.sleep(1)
                continue
    
    # 如果东方财富失败，尝试腾讯
    if df is None or len(df) == 0:
        try:
            prefix = 'sh' if symbol.startswith('6') else 'sz'
            df_daily = ak.stock_zh_a_hist_tx(
                symbol=f"{prefix}{symbol}",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"
            )
            if df_daily is not None and len(df_daily) > 0:
                # 聚合成月线
                df_daily['日期'] = pd.to_datetime(df_daily['日期'])
                df_daily.set_index('日期', inplace=True)
                df = df_daily.resample('ME').agg({
                    '开盘': 'first', '最高': 'max', '最低': 'min',
                    '收盘': 'last', '成交量': 'sum'
                }).dropna().reset_index()
                print("  (使用腾讯数据源)")
        except Exception as e:
            pass
    
    if df is None or len(df) == 0:
        print(f"  ❌ 所有数据源均失败")
        return
    
    if df is None or len(df) == 0:
        print(f"  ❌ 无数据返回")
        return
    
    print(f"  数据月份数: {len(df)}")
    
    # 计算MACD
    df = calculate_macd(df)
    df['MA55'] = df['收盘'].rolling(window=55).mean()
    
    # 取最新数据
    current = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else None
    
    # 取最近36个月
    df_36m = df.tail(36)
    max_dea_36m = df_36m['DEA'].max()
    
    current_dea = current['DEA']
    current_dif = current['DIF']
    current_close = current['收盘']
    current_ma55 = current['MA55']
    
    print(f"\n  【当前指标值】")
    print(f"    收盘价: {current_close:.2f}")
    ma55_str = f"{current_ma55:.2f}" if pd.notna(current_ma55) else 'N/A (数据不足55个月)'
    print(f"    MA55: {ma55_str}")
    print(f"    DIF: {current_dif:.4f}")
    print(f"    DEA: {current_dea:.4f}")
    print(f"    36个月DEA最高: {max_dea_36m:.4f}")
    
    print(f"\n  【条件检查】")
    
    # 条件1a: 历史有高度
    min_max_dea = 0.05
    c1a = max_dea_36m >= min_max_dea
    print(f"    1a. 历史DEA最高>{min_max_dea}: {max_dea_36m:.4f} -> {'✓ 通过' if c1a else '✗ 不通过'}")
    
    # 条件1b: 当前DEA从高位回落
    dea_drop_ratio = 0.7
    threshold_1b = dea_drop_ratio * max_dea_36m
    c1b = current_dea < threshold_1b
    print(f"    1b. 当前DEA<{dea_drop_ratio*100:.0f}%历史最高({threshold_1b:.4f}): {current_dea:.4f} -> {'✓ 通过' if c1b else '✗ 不通过'}")
    
    # 条件2a: DEA > 0
    c2a = current_dea > 0
    print(f"    2a. DEA>0: {current_dea:.4f} -> {'✓ 通过' if c2a else '✗ 不通过'}")
    
    # 条件2b: DIF > 0
    c2b = current_dif > 0
    print(f"    2b. DIF>0: {current_dif:.4f} -> {'✓ 通过' if c2b else '✗ 不通过'}")
    
    # 条件2c: DEA 接近零轴
    max_current_dea = 1.0
    c2c = current_dea < max_current_dea
    print(f"    2c. DEA<{max_current_dea}: {current_dea:.4f} -> {'✓ 通过' if c2c else '✗ 不通过'}")
    
    # 条件3: 金叉状态
    c3 = current_dif > current_dea
    print(f"    3.  金叉(DIF>DEA): DIF={current_dif:.4f} vs DEA={current_dea:.4f} -> {'✓ 通过' if c3 else '✗ 不通过'}")
    
    # 条件4: MA55存在
    c4 = pd.notna(current_ma55)
    print(f"    4.  MA55有效: {current_ma55:.2f if c4 else 'N/A'} -> {'✓ 通过' if c4 else '✗ 不通过 (需要55个月数据)'}")
    
    # 总结
    all_pass = all([c1a, c1b, c2a, c2b, c2c, c3, c4])
    print(f"\n  【结论】: {'✓ 符合条件!' if all_pass else '✗ 未通过筛选'}")
    
    # 统计各条件通过率
    return {
        'symbol': symbol,
        '数据月数': len(df),
        'c1a_历史高度': c1a,
        'c1b_DEA回落': c1b,
        'c2a_DEA>0': c2a,
        'c2b_DIF>0': c2b,
        'c2c_DEA<1': c2c,
        'c3_金叉': c3,
        'c4_MA55有效': c4,
        '全部通过': all_pass
    }


def main():
    print("月线MACD选股条件诊断")
    print("=" * 60)
    
    # 测试几只典型股票
    test_stocks = [
        ('000001', '平安银行'),
        ('000858', '五粮液'),
        ('600519', '贵州茅台'),
        ('300750', '宁德时代'),
        ('002415', '海康威视'),
        ('601318', '中国平安'),
        ('000002', '万科A'),
        ('600036', '招商银行'),
    ]
    
    results = []
    for symbol, name in test_stocks:
        result = diagnose_stock(symbol, name)
        if result:
            results.append(result)
    
    # 统计各条件通过率
    if results:
        df = pd.DataFrame(results)
        print("\n" + "=" * 60)
        print("【各条件通过率统计】")
        print("=" * 60)
        
        conditions = ['c1a_历史高度', 'c1b_DEA回落', 'c2a_DEA>0', 'c2b_DIF>0', 'c2c_DEA<1', 'c3_金叉', 'c4_MA55有效']
        for cond in conditions:
            rate = df[cond].sum() / len(df) * 100
            print(f"  {cond}: {rate:.0f}%")
        
        print(f"\n  全部通过: {df['全部通过'].sum()}/{len(df)}")


if __name__ == "__main__":
    main()
