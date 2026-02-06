#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
Date: 2026/02/02
Desc: 周线 MACD 零轴回踩起爆 + MA55贴近度排序

选股逻辑（周线数据）：
1. 历史有高度：52周内DEA最高值 > 0.05，当前DEA < 70%历史最高
2. 回踩零轴：DEA > 0, DIF > 0（多头），且 DEA < 1.0
3. 再次起跳：DIF > DEA（金叉状态）
4. 形态排序：按股价与周线MA55的贴近程度排序

目标：找周线大牛股回调后，在零轴企稳准备开启第二波行情
"""

import time
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd
from tqdm import tqdm


def calculate_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """
    计算 MACD 指标
    """
    close = df['收盘']
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    df['DIF'] = ema_fast - ema_slow
    df['DEA'] = df['DIF'].ewm(span=signal, adjust=False).mean()
    df['MACD柱'] = 2 * (df['DIF'] - df['DEA'])
    return df


def calculate_ma(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    计算月线均线（默认MA20，约一3年多的数据即可）
    """
    df[f'MA{period}'] = df['收盘'].rolling(window=period).mean()
    return df


# 全局数据源统计
DATA_SOURCE_STATS = {'eastmoney': 0, 'failed': 0}

def get_weekly_data(symbol: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    """
    获取周线数据 - 使用东方财富数据源
    """
    global DATA_SOURCE_STATS
    
    # 东方财富（直接周线，最快）
    for retry in range(3):
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="weekly",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"
            )
            if df is not None and len(df) >= 55:
                DATA_SOURCE_STATS['eastmoney'] += 1
                return df[['日期', '开盘', '最高', '最低', '收盘', '成交量']].copy()
        except:
            time.sleep(0.3)
            continue
    
    DATA_SOURCE_STATS['failed'] += 1
    return None


def screen_single_stock(
    symbol: str, 
    name: str, 
    end_date: str,
    # 参数配置
    min_max_dea: float = 0.05,     # 历史DEA最高值的最小要求
    dea_drop_ratio: float = 0.7,   # 当前DEA需小于历史最高的这个比例
    max_current_dea: float = 1.0,  # 当前DEA需小于此值（接近零轴）
) -> dict | None:
    """
    对单只股票进行筛选（周线数据）
    """
    try:
        # 获取3年的周线数据（约156周）
        start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=3*365)).strftime("%Y%m%d")
        
        # 获取周线数据
        df = get_weekly_data(symbol, start_date, end_date)
        
        if df is None or len(df) < 55:  # 数据不足55周
            return None
        
        # 计算指标
        df = calculate_macd(df.copy())
        df = calculate_ma(df, period=20)  # 改用MA20，数据要求更低
        
        # 取最近52周的数据（1年）
        df_52w = df.tail(52).copy()
        
        # 当前月（最新一行）
        current = df.iloc[-1]
        # 上个月
        prev = df.iloc[-2] if len(df) >= 2 else None
        
        # ========== 条件1: 历史有高度 ==========
        max_dea_52w = df_52w['DEA'].max()
        current_dea = current['DEA']
        
        # DEA 历史最高值必须显著为正
        if max_dea_52w < min_max_dea:
            return None
        
        # 当前 DEA 必须从高位回落
        if current_dea >= dea_drop_ratio * max_dea_52w:
            return None
        
        # ========== 条件2: 回踩零轴 ==========
        current_dif = current['DIF']
        
        # DEA 和 DIF 必须为正（大趋势多头）
        if current_dea <= 0 or current_dif <= 0:
            return None
        
        # DEA 必须接近零轴
        if current_dea >= max_current_dea:
            return None
        
        # ========== 条件3: 再次起跳 ==========
        # 条件3a: 金叉状态 (DIF > DEA)
        if current_dif <= current_dea:
            return None
        
        # 条件3b: DIF 拐头向上（本月DIF > 上月DIF）- 已放宽，改为可选
        # if prev is not None:
        #     prev_dif = prev['DIF']
        #     if pd.notna(prev_dif) and current_dif <= prev_dif:
        #         return None
        prev_dif = prev['DIF'] if prev is not None else None
        
        # ========== 计算 MA20 贴近度 ==========
        ma20 = current['MA20']
        close = current['收盘']
        
        if pd.isna(ma20):
            return None
        
        # 贴近度 = |MA20 - 收盘价| / 收盘价
        abs_gap_percent = abs(ma20 - close) / close
        
        # ========== 通过所有条件，记录结果 ==========
        current_macd = current['MACD柱']
        prev_macd = prev['MACD柱'] if prev is not None else None
        
        # 判断 MACD 柱状态
        if prev_macd is not None and pd.notna(prev_macd):
            if current_macd > 0 and prev_macd <= 0:
                macd_status = "翻红"
            elif current_macd > prev_macd:
                macd_status = "红柱放大" if current_macd > 0 else "绿柱缩短"
            else:
                macd_status = "红柱缩短" if current_macd > 0 else "绿柱放大"
        else:
            macd_status = "-"
        
        return {
            '代码': symbol,
            '名称': name,
            '收盘价': round(close, 2),
            'MA20': round(ma20, 2),
            'MA20贴近度%': round(abs_gap_percent * 100, 2),
            '位置': '上方' if close > ma20 else '下方',
            '当前DEA': round(current_dea, 4),
            '当前DIF': round(current_dif, 4),
            '历史最高DEA': round(max_dea_52w, 4),
            'DEA回落比%': round(current_dea / max_dea_52w * 100, 1),
            'MACD状态': macd_status,
        }
        
    except Exception as e:
        return None


def run_screener(top_n: int = 20, sample_ratio: float = 1.0) -> pd.DataFrame:
    """
    运行选股器
    :param top_n: 返回前N名
    :param sample_ratio: 抽样比例，0.01 表示 1%，用于快速测试
    :return: 筛选结果 DataFrame
    """
    print("=" * 70)
    print("周线 MACD 零轴回踩起爆形态选股")
    print("=" * 70)
    print("\n【选股条件】（全部基于周线数据）")
    print("  1. 历史有高度: 52周内DEA最高值 > 0.05，当前DEA < 70%历史最高")
    print("  2. 回踩零轴: DEA > 0, DIF > 0 (多头)，且 DEA < 1.0")
    print("  3. 再次起跳: DIF > DEA (金叉状态)")
    print("  4. 排序标准: 按周线MA55贴近度排序（越小越贴近均线）")
    
    # 获取当前日期
    end_date = datetime.now().strftime("%Y%m%d")
    print(f"\n筛选日期: {end_date}")
    
    # 获取 A 股列表
    print("\n[1/3] 获取 A 股列表...")
    stock_list = ak.stock_info_a_code_name()
    stock_list.columns = ['代码', '名称']
    
    # 过滤掉 ST 股票
    stock_list = stock_list[~stock_list['名称'].str.contains('ST|退', na=False)]
    
    # 只保留主板、创业板、科创板
    stock_list = stock_list[stock_list['代码'].str.match(r'^(00|30|60|68)')]
    
    # 抽样（用于快速测试）
    if sample_ratio < 1.0:
        stock_list = stock_list.sample(frac=sample_ratio, random_state=42)
        print(f"   [测试模式] 抽样 {sample_ratio*100:.0f}% 股票")
    
    print(f"   待筛选股票数量: {len(stock_list)}")
    
    # 遍历筛选
    print("\n[2/3] 逐只股票筛选（周线数据）...")
    print("   数据源: 东方财富 (stock_zh_a_hist)")
    results = []
    
    for _, row in tqdm(stock_list.iterrows(), total=len(stock_list), desc="筛选进度"):
        symbol = row['代码']
        name = row['名称']
        
        result = screen_single_stock(symbol, name, end_date)
        if result:
            results.append(result)
        
        # 避免请求过快
        time.sleep(0.1)
    
    print(f"\n   符合条件的股票: {len(results)} 只")
    print(f"   数据源统计: 东方财富成功 {DATA_SOURCE_STATS['eastmoney']} 只, 失败 {DATA_SOURCE_STATS['failed']} 只")
    
    # 转换为 DataFrame 并排序
    print("\n[3/3] 排序输出...")
    if not results:
        print("没有找到符合条件的股票")
        return pd.DataFrame()
    
    result_df = pd.DataFrame(results)
    
    # 按 MA55贴近度 排序（越小越贴近均线）
    result_df = result_df.sort_values('MA55贴近度%', ascending=True)
    result_df = result_df.reset_index(drop=True)
    result_df.index = result_df.index + 1
    result_df.index.name = '排名'
    
    # 输出前 N 名
    top_df = result_df.head(top_n)
    
    print("\n" + "=" * 70)
    print(f"【筛选结果】前 {min(top_n, len(result_df))} 名（按月线MA55贴近度排序）")
    print("=" * 70)
    print(top_df.to_string())
    
    # 保存完整结果
    output_file = f"monthly_macd_screener_{end_date}.csv"
    result_df.to_csv(output_file, encoding='utf-8-sig')
    print(f"\n完整结果已保存至: {output_file}")
    
    print("\n【指标说明】")
    print("  - MA55贴近度%: 股价与月线55均线的偏离程度，越小越贴近")
    print("  - 位置: 股价在MA55上方还是下方")
    print("  - DEA回落比%: 当前DEA占历史最高DEA的百分比，越小说明回调越充分")
    print("  - MACD状态: 翻红=刚金叉, 红柱放大=动能增强")
    
    return top_df


if __name__ == "__main__":
    # 快速测试：只筛选 1% 的股票
    # result = run_screener(top_n=20, sample_ratio=0.01)
    
    # 10% 测试
    # result = run_screener(top_n=20, sample_ratio=0.10)
    
    # 全量运行
    # result = run_screener(top_n=20, sample_ratio=1.0)
    
    # 默认：1% 快速测试
    result = run_screener(top_n=20, sample_ratio=0.01)
