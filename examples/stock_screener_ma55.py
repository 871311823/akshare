#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
Date: 2026/02/02
Desc: 量化选股脚本 - MACD趋势过滤 + MA55贴近度排序

选股逻辑：
1. 趋势过滤：MACD 的 DEA > 0
2. 形态排序：股价与55日均线的贴近程度
3. 输出前20名
"""

import time
import random
from datetime import datetime, timedelta

import pandas as pd
from tqdm import tqdm

# 模拟数据模式开关（网络不通时使用）
USE_MOCK_DATA = False

import akshare as ak


def calculate_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """
    计算 MACD 指标
    :param df: 包含 '收盘' 列的 DataFrame
    :param fast: 快线周期，默认12
    :param slow: 慢线周期，默认26
    :param signal: 信号线周期，默认9
    :return: 添加了 DIF, DEA, MACD 列的 DataFrame
    """
    close = df['收盘']
    # 计算 EMA
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    # DIF = 快线 - 慢线
    df['DIF'] = ema_fast - ema_slow
    # DEA = DIF 的 EMA
    df['DEA'] = df['DIF'].ewm(span=signal, adjust=False).mean()
    # MACD 柱状图
    df['MACD'] = 2 * (df['DIF'] - df['DEA'])
    return df


def calculate_ma(df: pd.DataFrame, period: int = 55) -> pd.DataFrame:
    """
    计算移动平均线
    :param df: 包含 '收盘' 列的 DataFrame
    :param period: 均线周期，默认55
    :return: 添加了 MA 列的 DataFrame
    """
    df[f'MA{period}'] = df['收盘'].rolling(window=period).mean()
    return df


def screen_single_stock(symbol: str, name: str, end_date: str) -> dict | None:
    """
    对单只股票进行筛选
    :param symbol: 股票代码
    :param name: 股票名称
    :param end_date: 截止日期
    :return: 符合条件返回字典，否则返回 None
    """
    try:
        if USE_MOCK_DATA:
            # 生成模拟历史数据
            dates = pd.date_range(end=end_date, periods=100, freq='D')
            base_price = random.uniform(10, 100)
            # 生成随机波动的价格序列
            prices = [base_price]
            for _ in range(99):
                change = random.uniform(-0.05, 0.05)
                prices.append(prices[-1] * (1 + change))
            df = pd.DataFrame({
                '日期': dates,
                '收盘': prices
            })
        else:
            # 获取足够长的历史数据用于计算指标（至少需要55+26天）
            start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")
            
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"  # 前复权
            )
        
        if df is None or len(df) < 60:  # 数据不足
            return None
        
        # 计算指标
        df = calculate_macd(df)
        df = calculate_ma(df, period=55)
        
        # 取最新一行数据
        latest = df.iloc[-1]
        
        # 检查数据有效性
        if pd.isna(latest['DEA']) or pd.isna(latest['MA55']) or pd.isna(latest['收盘']):
            return None
        
        # 趋势过滤：DEA > 0
        if latest['DEA'] <= 0:
            return None
        
        # 计算贴近程度
        close = latest['收盘']
        ma55 = latest['MA55']
        abs_gap_percent = abs(ma55 - close) / close
        
        return {
            '代码': symbol,
            '名称': name,
            '收盘价': round(close, 2),
            'MA55': round(ma55, 2),
            'DEA': round(latest['DEA'], 4),
            'DIF': round(latest['DIF'], 4),
            '贴近度%': round(abs_gap_percent * 100, 2),
            '位置': '上方' if close > ma55 else '下方'
        }
        
    except Exception as e:
        # 静默处理异常，避免中断整体流程
        return None


def run_screener(top_n: int = 20, sample_ratio: float = 1.0) -> pd.DataFrame:
    """
    运行选股器
    :param top_n: 返回前N名
    :param sample_ratio: 抽样比例，0.01 表示 1%，用于快速测试
    :return: 筛选结果 DataFrame
    """
    print("=" * 60)
    print("量化选股脚本 - MACD趋势过滤 + MA55贴近度排序")
    print("=" * 60)
    
    # 获取当前日期作为截止日期
    end_date = datetime.now().strftime("%Y%m%d")
    print(f"\n筛选日期: {end_date}")
    
    # 获取 A 股列表
    print("\n[1/3] 获取 A 股列表...")
    
    if USE_MOCK_DATA:
        print("   [模拟数据模式] 使用随机生成的测试数据")
        # 生成模拟股票列表
        mock_stocks = []
        for i in range(100):
            prefix = random.choice(['000', '002', '300', '600', '601', '688'])
            code = f"{prefix}{str(i).zfill(3)}"
            mock_stocks.append({'代码': code, '名称': f'测试股票{i}'})
        stock_list = pd.DataFrame(mock_stocks)
    else:
        # 使用更稳定的接口获取股票列表
        stock_list = ak.stock_info_a_code_name()
        stock_list.columns = ['代码', '名称']
        # 过滤掉 ST 股票
        stock_list = stock_list[~stock_list['名称'].str.contains('ST|退', na=False)]
        # 只保留主板、创业板、科创板（排除北交所等）
        stock_list = stock_list[stock_list['代码'].str.match(r'^(00|30|60|68)')]
    
    # 抽样（用于快速测试）
    if sample_ratio < 1.0:
        stock_list = stock_list.sample(frac=sample_ratio, random_state=42)
        print(f"   [测试模式] 抽样 {sample_ratio*100:.0f}% 股票")
    
    print(f"   待筛选股票数量: {len(stock_list)}")
    
    # 遍历筛选
    print("\n[2/3] 逐只股票筛选（这可能需要一些时间）...")
    results = []
    
    for _, row in tqdm(stock_list.iterrows(), total=len(stock_list), desc="筛选进度"):
        symbol = row['代码']
        name = row['名称']
        
        result = screen_single_stock(symbol, name, end_date)
        if result:
            results.append(result)
        
        # 避免请求过快被限流
        time.sleep(0.1)
    
    print(f"\n   符合 DEA > 0 条件的股票: {len(results)} 只")
    
    # 转换为 DataFrame 并排序
    print("\n[3/3] 排序输出...")
    if not results:
        print("没有找到符合条件的股票")
        return pd.DataFrame()
    
    result_df = pd.DataFrame(results)
    result_df = result_df.sort_values('贴近度%', ascending=True)
    result_df = result_df.reset_index(drop=True)
    result_df.index = result_df.index + 1  # 排名从1开始
    result_df.index.name = '排名'
    
    # 输出前 N 名
    top_df = result_df.head(top_n)
    
    print("\n" + "=" * 60)
    print(f"【筛选结果】前 {top_n} 名（按MA55贴近度排序）")
    print("=" * 60)
    print(top_df.to_string())
    
    # 保存完整结果
    output_file = f"screener_result_{end_date}.csv"
    result_df.to_csv(output_file, encoding='utf-8-sig')
    print(f"\n完整结果已保存至: {output_file}")
    
    return top_df


if __name__ == "__main__":
    # 快速测试：只筛选 1% 的股票
    # result = run_screener(top_n=20, sample_ratio=0.01)
    
    # 正式运行：筛选全部股票
    # result = run_screener(top_n=20, sample_ratio=1.0)
    
    # 默认：测试模式
    result = run_screener(top_n=20, sample_ratio=0.01)
