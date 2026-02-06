#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
测试 AkShare 各个A股历史数据接口的可用性、稳定性和速度
"""

import time
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd


def test_data_source(name: str, fetch_func, test_symbols: list[str]) -> dict:
    """
    测试单个数据源
    """
    results = {
        'name': name,
        'success': 0,
        'fail': 0,
        'total_time': 0,
        'errors': [],
    }
    
    for symbol in test_symbols:
        start_time = time.time()
        try:
            df = fetch_func(symbol)
            elapsed = time.time() - start_time
            
            if df is not None and len(df) > 0:
                results['success'] += 1
                results['total_time'] += elapsed
            else:
                results['fail'] += 1
                results['errors'].append(f"{symbol}: 返回空数据")
        except Exception as e:
            elapsed = time.time() - start_time
            results['fail'] += 1
            results['total_time'] += elapsed
            error_msg = str(e)[:80]
            results['errors'].append(f"{symbol}: {error_msg}")
        
        time.sleep(0.2)  # 避免请求过快
    
    total = results['success'] + results['fail']
    results['success_rate'] = results['success'] / total * 100 if total > 0 else 0
    results['avg_time'] = results['total_time'] / total if total > 0 else 0
    
    return results


def main():
    print("=" * 70)
    print("AkShare A股历史数据接口 稳定性测试")
    print("=" * 70)
    
    # 测试股票列表（覆盖不同板块）
    test_symbols = [
        '000001',  # 平安银行 (深主板)
        '000858',  # 五粮液 (深主板)
        '002415',  # 海康威视 (中小板)
        '300750',  # 宁德时代 (创业板)
        '600519',  # 贵州茅台 (沪主板)
        '601318',  # 中国平安 (沪主板)
        '688981',  # 中芯国际 (科创板)
        '603259',  # 药明康德 (沪主板)
    ]
    
    print(f"\n测试股票: {', '.join(test_symbols)}")
    print(f"测试数量: {len(test_symbols)} 只\n")
    
    # 日期范围
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=365*5)).strftime("%Y%m%d")
    
    # 定义各数据源的获取函数
    data_sources = {}
    
    # 1. 东方财富 (stock_zh_a_hist)
    def fetch_eastmoney(symbol):
        return ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
    data_sources['东方财富 (stock_zh_a_hist)'] = fetch_eastmoney
    
    # 2. 新浪 (stock_zh_a_daily)
    def fetch_sina(symbol):
        prefix = 'sh' if symbol.startswith('6') else 'sz'
        return ak.stock_zh_a_daily(symbol=f"{prefix}{symbol}", start_date=start_date, end_date=end_date, adjust="qfq")
    data_sources['新浪 (stock_zh_a_daily)'] = fetch_sina
    
    # 3. 腾讯 (stock_zh_a_hist_tx)
    def fetch_tencent(symbol):
        prefix = 'sh' if symbol.startswith('6') else 'sz'
        return ak.stock_zh_a_hist_tx(symbol=f"{prefix}{symbol}", start_date=start_date, end_date=end_date, adjust="qfq")
    data_sources['腾讯 (stock_zh_a_hist_tx)'] = fetch_tencent
    
    # 4. 网易 (stock_zh_a_hist_163)  
    def fetch_163(symbol):
        return ak.stock_zh_a_hist_163(symbol=symbol, start_date=start_date, end_date=end_date, adjust="qfq")
    data_sources['网易 (stock_zh_a_hist_163)'] = fetch_163
    
    # 5. 百度股市通 (stock_zh_a_hist_min_em) - 仅支持分钟级别，跳过
    
    # 测试各数据源
    all_results = []
    
    for name, fetch_func in data_sources.items():
        print(f"\n{'─' * 50}")
        print(f"测试: {name}")
        print(f"{'─' * 50}")
        
        result = test_data_source(name, fetch_func, test_symbols)
        all_results.append(result)
        
        print(f"  成功: {result['success']}/{len(test_symbols)}")
        print(f"  成功率: {result['success_rate']:.1f}%")
        print(f"  平均耗时: {result['avg_time']:.2f}s")
        
        if result['errors']:
            print(f"  错误示例: {result['errors'][0][:60]}...")
    
    # 汇总排名
    print("\n" + "=" * 70)
    print("【数据源稳定性排名】")
    print("=" * 70)
    
    # 按成功率和速度综合排序
    df_results = pd.DataFrame(all_results)
    df_results['score'] = df_results['success_rate'] * 0.7 + (10 - df_results['avg_time'].clip(upper=10)) * 3
    df_results = df_results.sort_values('score', ascending=False)
    
    print(f"\n{'排名':<4} {'数据源':<30} {'成功率':<10} {'平均耗时':<10} {'综合评分':<10}")
    print("-" * 70)
    
    for i, (_, row) in enumerate(df_results.iterrows(), 1):
        print(f"{i:<4} {row['name']:<30} {row['success_rate']:.1f}%{'':<5} {row['avg_time']:.2f}s{'':<5} {row['score']:.1f}")
    
    # 推荐
    best = df_results.iloc[0]
    print(f"\n【推荐数据源】: {best['name']}")
    print(f"  - 成功率: {best['success_rate']:.1f}%")
    print(f"  - 平均耗时: {best['avg_time']:.2f}s")
    
    return df_results


if __name__ == "__main__":
    main()
