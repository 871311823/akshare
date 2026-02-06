#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
统一选股器 - 多策略Tab页
Tab1: 周线 MACD 零轴回踩选股
Tab2: 月线 MACD + 基本面筛选
"""

import os
import time
import threading
from datetime import datetime, timedelta
from typing import Optional
from threading import Lock

from flask import Flask, render_template_string, jsonify, request, send_file
from io import BytesIO

import akshare as ak
import pandas as pd
import numpy as np

app = Flask(__name__)

# 线程锁，防止并发冲突
API_LOCK = Lock()
TASK_LOCKS = {
    'tab1': Lock(),
    'tab2': Lock()
}

# =========================== Tab1: 周线MACD策略 ===========================
TAB1_MODES = {
    'strict': {
        'name': '严格模式',
        'ma_min': 0, 'ma_max': 0.08,
        'history_dea': 0.3, 'pullback': 0.3,
        'dea_min': 0, 'dea_max': 0.5,
    },
    'default': {
        'name': '默认模式',
        'ma_min': -0.05, 'ma_max': 0.10,
        'history_dea': 0.1, 'pullback': 0.5,
        'dea_min': 0, 'dea_max': 1.0,
    },
    'loose': {
        'name': '宽松模式',
        'ma_min': -0.10, 'ma_max': 0.20,
        'history_dea': 0.05, 'pullback': 0.7,
        'dea_min': -0.2, 'dea_max': 1.5,
    }
}

# =========================== Tab2: 月线基本面策略 ===========================
TAB2_MODES = {
    'strict': {
        'name': '严格模式',
        'pb_max': 0.8, 'pb_min': 0.1,
        'market_cap_min': 800, 'roa_min': 0.20, 'beta_max': 0.6,
    },
    'default': {
        'name': '默认模式',
        'pb_max': 1.0, 'pb_min': 0.0,
        'market_cap_min': 500, 'roa_min': 0.15, 'beta_max': 0.7,
    },
    'loose': {
        'name': '宽松模式',
        'pb_max': 1.5, 'pb_min': 0.0,
        'market_cap_min': 300, 'roa_min': 0.10, 'beta_max': 0.8,
    }
}

# 全局状态
STATE = {
    'tab1': {
        'status': 'idle', 'progress': 0, 'total': 0, 'current_stock': '',
        'data_source': '自动选择', 'results': [], 
        'stats': {'success': 0, 'failed': 0, 'matched': 0},
        'message': '', 'mode': 'default', 'golden_only': False
    },
    'tab2': {
        'status': 'idle', 'progress': 0, 'total': 0, 'current_stock': '',
        'data_source': '东方财富', 'results': [],
        'stats': {'success': 0, 'failed': 0, 'matched': 0},
        'message': '', 'mode': 'default', 'market_macd': 0
    }
}

DATA_SOURCE_HEALTH = {"eastmoney_ok": None}


# ==================== 通用函数 ====================
def calculate_macd(df, fast=12, slow=26, signal=9):
    close = df['收盘']
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    df['DIF'] = ema_fast - ema_slow
    df['DEA'] = df['DIF'].ewm(span=signal, adjust=False).mean()
    df['MACD柱'] = 2 * (df['DIF'] - df['DEA'])
    return df


# ==================== Tab1: 周线MACD ====================
def get_weekly_data_tab1(symbol, start_date, end_date):
    global DATA_SOURCE_HEALTH
    
    # 使用线程锁保护API调用
    with API_LOCK:
        # 东方财富周线
        if DATA_SOURCE_HEALTH.get("eastmoney_ok") is not False:
            try:
                df = ak.stock_zh_a_hist(
                    symbol=symbol, period="weekly",
                    start_date=start_date, end_date=end_date,
                    adjust="qfq", timeout=10,
                )
                if df is not None and len(df) >= 55:
                    DATA_SOURCE_HEALTH["eastmoney_ok"] = True
                    STATE['tab1']["data_source"] = "东方财富 (stock_zh_a_hist)"
                    return df[["日期", "开盘", "最高", "最低", "收盘", "成交量"]].copy()
            except Exception:
                DATA_SOURCE_HEALTH["eastmoney_ok"] = False
        
        # 腾讯日线聚合
        for retry in range(2):
            try:
                prefix = "sh" if symbol.startswith("6") else "sz"
                df_daily = ak.stock_zh_a_hist_tx(
                    symbol=f"{prefix}{symbol}",
                    start_date=start_date, end_date=end_date,
                    adjust="qfq", timeout=10,
                )
                if df_daily is None or df_daily.empty:
                    continue
                
                df_daily = df_daily.copy()
                df_daily["date"] = pd.to_datetime(df_daily["date"], errors="coerce")
                df_daily = df_daily.dropna(subset=["date"]).set_index("date").sort_index()
                
                df_weekly = (
                    df_daily.resample("W-FRI")
                    .agg({
                        "open": "first", "high": "max", "low": "min",
                        "close": "last", "amount": "sum",
                    })
                    .dropna().reset_index()
                )
                
                df_weekly = df_weekly.rename(columns={
                    "date": "日期", "open": "开盘", "high": "最高",
                    "low": "最低", "close": "收盘", "amount": "成交量",
                })
                
                df_weekly["日期"] = pd.to_datetime(df_weekly["日期"], errors="coerce").dt.date
                STATE['tab1']["data_source"] = "腾讯 (stock_zh_a_hist_tx → 周线聚合)"
                return df_weekly[["日期", "开盘", "最高", "最低", "收盘", "成交量"]].copy()
            except Exception:
                if retry < 1:
                    time.sleep(0.5)
                    continue
        
        return None


def screen_stock_tab1(symbol, name, end_date, mode='default', golden_only=False):
    params = TAB1_MODES[mode]
    
    try:
        start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=3*365)).strftime("%Y%m%d")
        df = get_weekly_data_tab1(symbol, start_date, end_date)
        if df is None or len(df) < 100:
            return None, 'no_data'
        
        df = calculate_macd(df.copy())
        df['MA55'] = df['收盘'].rolling(window=55).mean()
        
        current = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else None
        
        ma55, close = current['MA55'], current['收盘']
        dea, dif = current['DEA'], current['DIF']
        macd = current['MACD柱']
        prev_macd = prev['MACD柱'] if prev is not None else 0
        
        if pd.isna(ma55): return None, 'no_ma'
        
        # 条件筛选
        deviation = (close - ma55) / ma55
        if deviation < params['ma_min'] or deviation >= params['ma_max']:
            return None, 'ma_filter'
        
        max_dea_100w = df.tail(100)['DEA'].max()
        if max_dea_100w <= params['history_dea'] or dea >= params['pullback'] * max_dea_100w:
            return None, 'dea_filter'
        
        if dea <= params['dea_min'] or dea >= params['dea_max']:
            return None, 'dea_range'
        
        is_golden = dif > dea
        if golden_only and not is_golden:
            return None, 'no_golden'
        
        signal = "金叉" if is_golden else ("绿柱缩短" if (macd > prev_macd and macd < 0) else "待金叉")
        
        return {
            '代码': symbol, '名称': name, '收盘价': round(close, 2),
            'MA55': round(ma55, 2), '偏离度%': round(deviation * 100, 2),
            'DEA': round(dea, 4), '历史DEA': round(max_dea_100w, 4),
            '回落比%': round((1 - dea / max_dea_100w) * 100, 1),
            '信号': signal
        }, 'matched'
    except Exception:
        return None, 'error'


# ==================== Tab2: 月线基本面 ====================
def get_monthly_data_tab2(symbol, start_date, end_date):
    # 月线数据获取添加重试
    for retry in range(2):
        with API_LOCK:
            try:
                df = ak.stock_zh_a_hist(
                    symbol=symbol, period="monthly",
                    start_date=start_date, end_date=end_date,
                    adjust="qfq", timeout=15,  # 增加超时时间
                )
                if df is None or df.empty:
                    if retry < 1:
                        time.sleep(0.5)
                        continue
                    return None
                return df[["日期", "开盘", "最高", "最低", "收盘", "成交量"]].copy()
            except Exception:
                if retry < 1:
                    time.sleep(0.5)
                    continue
                return None
    return None


def get_market_macd_signal():
    with API_LOCK:
        try:
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=5*365)).strftime("%Y%m%d")
            
            df = ak.stock_zh_index_hist(
                symbol="000300", period="monthly",
                start_date=start_date, end_date=end_date,
            )
            
            if df is None or len(df) < 50:
                return 0
            
            df = calculate_macd(df.copy())
            last_macd = df.iloc[-1]['MACD柱']
            STATE['tab2']['market_macd'] = round(last_macd, 4)
            return last_macd
        except Exception:
            return 0


def calculate_beta(symbol, end_date):
    # Beta计算添加重试和缓存
    for retry in range(2):  # Beta计算重试2次
        with API_LOCK:
            try:
                time0 = datetime.strptime(end_date, "%Y%m%d")
                time1 = datetime(time0.year - 1, time0.month, time0.day)
                
                index_data = ak.stock_zh_index_hist(
                    symbol="000300", period="daily",
                    start_date=time1.strftime("%Y%m%d"),
                    end_date=time0.strftime("%Y%m%d"),
                )
                
                if index_data is None or len(index_data) < 100:
                    if retry < 1:
                        time.sleep(0.5)
                        continue
                    return None
                
                index_returns = index_data['收盘'].pct_change()
                index_var = index_returns.var()
                
                stock_data = ak.stock_zh_a_hist(
                    symbol=symbol, period="daily",
                    start_date=time1.strftime("%Y%m%d"),
                    end_date=time0.strftime("%Y%m%d"),
                    adjust="qfq",
                    timeout=15,  # 增加超时时间
                )
                
                if stock_data is None or len(stock_data) < 100:
                    if retry < 1:
                        time.sleep(0.5)
                        continue
                    return None
                
                stock_returns = stock_data['收盘'].pct_change()
                cov = index_returns.cov(stock_returns)
                beta = cov / index_var
                return beta
            except Exception as e:
                if retry < 1:
                    time.sleep(0.5)
                    continue
                return None
    return None


def screen_stock_tab2(symbol, name, end_date, mode='default'):
    params = TAB2_MODES[mode]
    debug_info = {'symbol': symbol, 'name': name}
    
    try:
        # 基本面数据 - 添加重试机制
        pb_ratio = None
        market_cap = 0
        
        for retry in range(3):  # 重试3次
            try:
                with API_LOCK:
                    df_fundamental = ak.stock_individual_info_em(symbol=symbol)
                    time.sleep(0.2)  # 每次请求后等待200ms
                
                if df_fundamental is None or df_fundamental.empty:
                    if retry < 2:
                        time.sleep(1)  # 失败后等待1秒再重试
                        continue
                    debug_info['fail_reason'] = 'no_fundamental_data_after_retry'
                    print(f"[DEBUG] {symbol} {name}: 无基本面数据(重试{retry+1}次)")
                    return None, 'no_fundamental'
                
                fundamental_dict = dict(zip(df_fundamental['item'], df_fundamental['value']))
                
                # 处理市值
                market_cap_str = fundamental_dict.get('总市值', '0')
                if isinstance(market_cap_str, str):
                    market_cap = float(market_cap_str.replace(',', '')) / 100000000 if market_cap_str else 0
                else:
                    market_cap = float(market_cap_str) / 100000000 if market_cap_str else 0
                
                # 处理市净率
                pb_ratio_str = fundamental_dict.get('市净率', '0')
                if pb_ratio_str and pb_ratio_str != '-':
                    if isinstance(pb_ratio_str, str):
                        pb_ratio = float(pb_ratio_str)
                    else:
                        pb_ratio = float(pb_ratio_str)
                else:
                    pb_ratio = None
                
                debug_info['market_cap'] = market_cap
                debug_info['pb_ratio'] = pb_ratio
                break  # 成功获取,跳出重试循环
                
            except Exception as e:
                if retry < 2:
                    time.sleep(1)  # 失败后等待1秒再重试
                    continue
                debug_info['fail_reason'] = f'fundamental_error: {str(e)}'
                print(f"[DEBUG] {symbol} {name}: 基本面获取异常(重试{retry+1}次) - {str(e)}")
                return None, 'no_fundamental'
        
        # 筛选
        if pb_ratio is None:
            debug_info['fail_reason'] = 'pb_is_none'
            print(f"[DEBUG] {symbol} {name}: PB为空")
            return None, 'pb_filter'
        
        if pb_ratio <= params['pb_min'] or pb_ratio >= params['pb_max']:
            debug_info['fail_reason'] = f'pb_out_of_range: {pb_ratio} (要求{params["pb_min"]}~{params["pb_max"]})'
            print(f"[DEBUG] {symbol} {name}: PB={pb_ratio} 不在范围{params['pb_min']}~{params['pb_max']}")
            return None, 'pb_filter'
        
        if market_cap < params['market_cap_min']:
            debug_info['fail_reason'] = f'market_cap_too_small: {market_cap}亿 (要求>{params["market_cap_min"]}亿)'
            print(f"[DEBUG] {symbol} {name}: 市值{market_cap}亿 < {params['market_cap_min']}亿")
            return None, 'market_cap_filter'
        
        beta = calculate_beta(symbol, end_date)
        debug_info['beta'] = beta
        
        if beta is None:
            debug_info['fail_reason'] = 'beta_calculation_failed'
            print(f"[DEBUG] {symbol} {name}: Beta计算失败")
            return None, 'beta_filter'
        
        if beta >= params['beta_max']:
            debug_info['fail_reason'] = f'beta_too_high: {beta:.2f} (要求<{params["beta_max"]})'
            print(f"[DEBUG] {symbol} {name}: Beta={beta:.2f} >= {params['beta_max']}")
            return None, 'beta_filter'
        
        start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=3*365)).strftime("%Y%m%d")
        df = get_monthly_data_tab2(symbol, start_date, end_date)
        
        if df is None or len(df) < 50:
            debug_info['fail_reason'] = f'insufficient_monthly_data: {len(df) if df is not None else 0}条'
            print(f"[DEBUG] {symbol} {name}: 月线数据不足 ({len(df) if df is not None else 0}条)")
            return None, 'no_monthly_data'
        
        df = calculate_macd(df.copy())
        current = df.iloc[-1]
        
        dea, dif = current['DEA'], current['DIF']
        close = current['收盘']
        
        signal = "金叉" if dif > dea else "待金叉"
        
        print(f"[SUCCESS] {symbol} {name}: 通过所有筛选! PB={pb_ratio:.2f}, 市值={market_cap:.0f}亿, Beta={beta:.2f}")
        
        return {
            '代码': symbol, '名称': name, '收盘价': round(close, 2),
            '市值(亿)': round(market_cap, 2),
            'PB': round(pb_ratio, 2), 'Beta': round(beta, 2),
            'DEA': round(dea, 4), '信号': signal
        }, 'matched'
    except Exception as e:
        debug_info['fail_reason'] = f'exception: {str(e)}'
        print(f"[DEBUG] {symbol} {name}: 未知异常 - {str(e)}")
        return None, 'error'


# ==================== 后台任务 ====================
def run_task_tab1(ratio, mode, golden_only):
    # 检查是否已有任务在运行
    if not TASK_LOCKS['tab1'].acquire(blocking=False):
        STATE['tab1']['status'] = 'error'
        STATE['tab1']['message'] = 'Tab1任务已在运行中，请等待完成'
        return
    
    try:
        state = STATE['tab1']
        state.update({
            'status': 'running', 'progress': 0, 'results': [],
            'stats': {'success': 0, 'failed': 0, 'matched': 0},
            'message': '获取股票列表...', 'mode': mode, 'golden_only': golden_only
        })
        
        # 获取股票列表（使用锁保护）
        with API_LOCK:
            stocks = ak.stock_info_a_code_name()
        stocks.columns = ['代码', '名称']
        stocks = stocks[~stocks['名称'].str.contains('ST|退', na=False)]
        stocks = stocks[stocks['代码'].str.match(r'^(00|30|60|68)')]
        
        if ratio < 1.0:
            stocks = stocks.sample(frac=ratio, random_state=42)
        
        state['total'] = len(stocks)
        end_date = datetime.now().strftime("%Y%m%d")
        
        for i, (_, row) in enumerate(stocks.iterrows()):
            state['progress'] = i + 1
            state['current_stock'] = f"{row['代码']} {row['名称']}"
            
            result, status = screen_stock_tab1(row['代码'], row['名称'], end_date, mode, golden_only)
            if result:
                state['results'].append(result)
                state['stats']['matched'] += 1
                state['stats']['success'] += 1
            elif status in ['no_data', 'error']:
                state['stats']['failed'] += 1
            else:
                state['stats']['success'] += 1
            
            time.sleep(0.1)  # 减少API压力
        
        state['results'] = sorted(state['results'], key=lambda x: x['偏离度%'])
        state['status'] = 'completed'
        state['message'] = f'完成! 找到 {len(state["results"])} 只股票'
    except Exception as e:
        STATE['tab1']['status'] = 'error'
        STATE['tab1']['message'] = str(e)
    finally:
        TASK_LOCKS['tab1'].release()


def run_task_tab2(ratio, mode):
    # 检查是否已有任务在运行
    if not TASK_LOCKS['tab2'].acquire(blocking=False):
        STATE['tab2']['status'] = 'error'
        STATE['tab2']['message'] = 'Tab2任务已在运行中，请等待完成'
        return
    
    try:
        state = STATE['tab2']
        state.update({
            'status': 'running', 'progress': 0, 'results': [],
            'stats': {'success': 0, 'failed': 0, 'matched': 0},
            'message': '获取大盘MACD信号...', 'mode': mode
        })
        
        market_macd = get_market_macd_signal()
        if market_macd > 0:
            state['message'] = f'⚠️ 大盘MACD={state["market_macd"]}>0, 建议谨慎'
        else:
            state['message'] = f'✅ 大盘MACD={state["market_macd"]}≤0, 可筛选'
        
        # 获取股票列表（使用锁保护）
        with API_LOCK:
            stocks = ak.stock_info_a_code_name()
        stocks.columns = ['代码', '名称']
        stocks = stocks[~stocks['名称'].str.contains('ST|退', na=False)]
        stocks = stocks[stocks['代码'].str.match(r'^(00|30|60|68)')]
        
        if ratio < 1.0:
            stocks = stocks.sample(frac=ratio, random_state=42)
        
        state['total'] = len(stocks)
        end_date = datetime.now().strftime("%Y%m%d")
        
        for i, (_, row) in enumerate(stocks.iterrows()):
            state['progress'] = i + 1
            state['current_stock'] = f"{row['代码']} {row['名称']}"
            
            result, status = screen_stock_tab2(row['代码'], row['名称'], end_date, mode)
            if result:
                state['results'].append(result)
                state['stats']['matched'] += 1
                state['stats']['success'] += 1
            elif status in ['no_fundamental', 'no_monthly_data', 'error']:
                state['stats']['failed'] += 1
            else:
                state['stats']['success'] += 1
            
            time.sleep(0.15)  # Tab2需要更多API调用，间隔稍长
        
        state['results'] = sorted(state['results'], key=lambda x: x['市值(亿)'], reverse=True)
        state['status'] = 'completed'
        state['message'] = f'完成! 找到 {len(state["results"])} 只股票'
    except Exception as e:
        STATE['tab2']['status'] = 'error'
        STATE['tab2']['message'] = str(e)
    finally:
        TASK_LOCKS['tab2'].release()


# ==================== HTML模板 ====================
HTML = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>多策略选股器</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{font-family:system-ui,sans-serif;background:#f0f2f5;padding:20px}
        .container{max-width:1400px;margin:0 auto}
        .header{background:linear-gradient(135deg,#1a73e8,#e74c3c);color:#fff;padding:25px;border-radius:12px;margin-bottom:20px}
        .header h1{font-size:24px;margin-bottom:8px}
        .tabs{display:flex;gap:0;margin-bottom:20px;background:#fff;border-radius:12px;padding:4px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
        .tab{flex:1;padding:12px;text-align:center;cursor:pointer;border-radius:8px;font-weight:600;transition:all .3s}
        .tab:hover{background:#f0f2f5}
        .tab.active{background:linear-gradient(135deg,#1a73e8,#6c5ce7);color:#fff}
        .tab-content{display:none}
        .tab-content.active{display:block}
        .card{background:#fff;border-radius:12px;padding:20px;margin-bottom:20px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
        .card h2{font-size:15px;color:#333;margin-bottom:12px;padding-bottom:8px;border-bottom:2px solid #1a73e8}
        .conditions{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
        .cond{background:#f8f9fa;padding:10px;border-radius:6px;font-size:13px;border-left:3px solid #1a73e8}
        .btn{background:linear-gradient(135deg,#1a73e8,#6c5ce7);color:#fff;border:none;padding:12px 28px;border-radius:8px;cursor:pointer;font-size:15px;margin-right:8px}
        .btn:disabled{background:#ccc}
        select,input[type="checkbox"]{padding:10px;border-radius:8px;border:1px solid #ddd;margin-right:12px}
        .progress{height:22px;background:#e9ecef;border-radius:11px;overflow:hidden;margin:15px 0}
        .progress-bar{height:100%;background:linear-gradient(90deg,#1a73e8,#6c5ce7);transition:width .3s;color:#fff;font-size:12px;display:flex;align-items:center;justify-content:center}
        .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:15px 0}
        .stat{text-align:center;padding:12px;background:#f8f9fa;border-radius:8px}
        .stat-val{font-size:26px;font-weight:700;color:#1a73e8}
        .stat-lbl{font-size:11px;color:#666;margin-top:4px}
        .msg{padding:12px;background:#e3f2fd;border-radius:8px;color:#1565c0;margin:10px 0;font-size:14px}
        .warning{background:#fff3cd;color:#856404}
        table{width:100%;border-collapse:collapse;font-size:13px}
        th,td{padding:10px 6px;text-align:left;border-bottom:1px solid #eee}
        th{background:#f8f9fa;font-weight:600}
        tr:hover{background:#f8f9fa}
        .tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px}
        .up{background:#e8f5e9;color:#2e7d32}
        .down{background:#ffebee;color:#c62828}
        .hot{background:#fff3e0;color:#e65100}
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>🚀 多策略选股器</h1>
    </div>
    
    <div class="tabs">
        <div class="tab active" onclick="switchTab('tab1')">📈 Tab1: 周线MACD</div>
        <div class="tab" onclick="switchTab('tab2')">📊 Tab2: 月线基本面</div>
    </div>
    
    <!-- Tab1 Content -->
    <div id="tab1" class="tab-content active">
        <div class="card">
            <h2>选股条件(周线) - <span id="t1_mode">默认模式</span></h2>
            <div class="conditions">
                <div class="cond">1️⃣ 均线支撑: <span id="t1_c1">-5%~+15%</span></div>
                <div class="cond">2️⃣ 历史高度: DEA最高 > <span id="t1_c2">0.1</span></div>
                <div class="cond">3️⃣ 充分回调: 当前DEA < <span id="t1_c3">60%</span>历史最高</div>
                <div class="cond">4️⃣ 零轴企稳: DEA在 <span id="t1_c4">0~0.5</span></div>
                <div class="cond">5️⃣ 趋势反转: <span id="t1_c5">包含即将金叉</span></div>
            </div>
        </div>
        <div class="card">
            <h2>控制面板</h2>
            <select id="t1_mode_sel" onchange="updateTab1Mode()">
                <option value="strict">🔒 严格模式</option>
                <option value="default" selected>✅ 默认模式</option>
                <option value="loose">📦 宽松模式</option>
            </select>
            <label style="margin-right:12px">
                <input type="checkbox" id="t1_golden" onchange="updateTab1Golden()"> ✨ 仅已金叉
            </label>
            <select id="t1_ratio">
                <option value="0.01">1% (~50只)</option>
                <option value="0.05" selected>5% (~250只)</option>
                <option value="0.10">10% (~500只)</option>
                <option value="0.30">30% (~1500只)</option>
                <option value="0.50">50% (~2500只)</option>
                <option value="1.00">100% (全部)</option>
            </select>
            <button class="btn" id="t1_btn" onclick="startTab1()">🚀 开始筛选</button>
            <div class="progress" id="t1_pbox" style="display:none"><div class="progress-bar" id="t1_pbar">0%</div></div>
            <div class="stats" id="t1_sbox" style="display:none">
                <div class="stat"><div class="stat-val" id="t1_s1">0</div><div class="stat-lbl">总数</div></div>
                <div class="stat"><div class="stat-val" id="t1_s2">0</div><div class="stat-lbl">成功</div></div>
                <div class="stat"><div class="stat-val" id="t1_s3">0</div><div class="stat-lbl">失败</div></div>
                <div class="stat"><div class="stat-val" id="t1_s4">0</div><div class="stat-lbl">符合</div></div>
            </div>
            <div class="msg" id="t1_msg" style="display:none"></div>
        </div>
        <div class="card" id="t1_rcard" style="display:none">
            <h2>筛选结果 <button class="btn" onclick="exportExcel('tab1')" style="float:right;padding:6px 12px;font-size:12px">📊 导出Excel</button></h2>
            <table><thead><tr><th>#</th><th>代码</th><th>名称</th><th>收盘</th><th>MA55</th><th>偏离度</th><th>DEA</th><th>历史DEA</th><th>回落比</th><th>信号</th></tr></thead>
            <tbody id="t1_tbody"></tbody></table>
        </div>
    </div>
    
    <!-- Tab2 Content -->
    <div id="tab2" class="tab-content">
        <div class="card">
            <h2>选股条件(月线) - <span id="t2_mode">默认模式</span></h2>
            <div class="conditions">
                <div class="cond">1️⃣ 大盘择时: 沪深300月线MACD ≤ 0</div>
                <div class="cond">2️⃣ 市净率: <span id="t2_pb">0 < PB < 1.0</span></div>
                <div class="cond">3️⃣ 市值: > <span id="t2_cap">500</span>亿</div>
                <div class="cond">4️⃣ Beta(相对300): < <span id="t2_beta">0.7</span></div>
                <div class="cond">5️⃣ 趋势反转: 包含即将金叉</div>
            </div>
        </div>
        <div class="card">
            <h2>控制面板</h2>
            <select id="t2_mode_sel" onchange="updateTab2Mode()">
                <option value="strict">🔒 严格模式</option>
                <option value="default" selected>✅ 默认模式</option>
                <option value="loose">📦 宽松模式</option>
            </select>
            <select id="t2_ratio">
                <option value="0.01">1% (~50只)</option>
                <option value="0.05" selected>5% (~250只)</option>
                <option value="0.10">10% (~500只)</option>
                <option value="0.30">30% (~1500只)</option>
                <option value="0.50">50% (~2500只)</option>
                <option value="1.00">100% (全部)</option>
            </select>
            <button class="btn" id="t2_btn" onclick="startTab2()">🚀 开始筛选</button>
            <div class="progress" id="t2_pbox" style="display:none"><div class="progress-bar" id="t2_pbar">0%</div></div>
            <div class="stats" id="t2_sbox" style="display:none">
                <div class="stat"><div class="stat-val" id="t2_s1">0</div><div class="stat-lbl">总数</div></div>
                <div class="stat"><div class="stat-val" id="t2_s2">0</div><div class="stat-lbl">成功</div></div>
                <div class="stat"><div class="stat-val" id="t2_s3">0</div><div class="stat-lbl">失败</div></div>
                <div class="stat"><div class="stat-val" id="t2_s4">0</div><div class="stat-lbl">符合</div></div>
            </div>
            <div class="msg" id="t2_msg" style="display:none"></div>
        </div>
        <div class="card" id="t2_rcard" style="display:none">
            <h2>筛选结果 <button class="btn" onclick="exportExcel('tab2')" style="float:right;padding:6px 12px;font-size:12px">📊 导出Excel</button></h2>
            <table><thead><tr><th>#</th><th>代码</th><th>名称</th><th>收盘</th><th>市值</th><th>PB</th><th>Beta</th><th>DEA</th><th>信号</th></tr></thead>
            <tbody id="t2_tbody"></tbody></table>
        </div>
    </div>
</div>

<script>
let t1_timer, t2_timer;
const T1_MODES={
    strict:{name:'严格模式',c1:'0%~+8%',c2:'0.3',c3:'40%',c4:'0~0.15'},
    default:{name:'默认模式',c1:'-5%~+15%',c2:'0.1',c3:'60%',c4:'0~0.5'},
    loose:{name:'宽松模式',c1:'-10%~+25%',c2:'0.05',c3:'80%',c4:'-0.1~1.0'}
};
const T2_MODES={
    strict:{name:'严格模式',pb:'0.1~0.8',cap:'800',beta:'0.6'},
    default:{name:'默认模式',pb:'0~1.0',cap:'500',beta:'0.7'},
    loose:{name:'宽松模式',pb:'0~1.5',cap:'300',beta:'0.8'}
};

function switchTab(tab){
    document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
    event.target.classList.add('active');
    document.getElementById(tab).classList.add('active');
}

function updateTab1Mode(){
    let m=document.getElementById('t1_mode_sel').value;
    let p=T1_MODES[m];
    document.getElementById('t1_mode').textContent=p.name;
    document.getElementById('t1_c1').textContent=p.c1;
    document.getElementById('t1_c2').textContent=p.c2;
    document.getElementById('t1_c3').textContent=p.c3;
    document.getElementById('t1_c4').textContent=p.c4;
}

function updateTab1Golden(){
    let checked=document.getElementById('t1_golden').checked;
    document.getElementById('t1_c5').textContent=checked?'仅已金叉':'包含即将金叉';
}

function updateTab2Mode(){
    let m=document.getElementById('t2_mode_sel').value;
    let p=T2_MODES[m];
    document.getElementById('t2_mode').textContent=p.name;
    document.getElementById('t2_pb').textContent=p.pb;
    document.getElementById('t2_cap').textContent=p.cap;
    document.getElementById('t2_beta').textContent=p.beta;
}

function startTab1(){
    document.getElementById('t1_btn').disabled=true;
    document.getElementById('t1_pbox').style.display='block';
    document.getElementById('t1_sbox').style.display='grid';
    document.getElementById('t1_rcard').style.display='none';
    let m=document.getElementById('t1_mode_sel').value;
    let r=document.getElementById('t1_ratio').value;
    let g=document.getElementById('t1_golden').checked?'1':'0';
    fetch('/start/tab1?ratio='+r+'&mode='+m+'&golden='+g);
    t1_timer=setInterval(pollTab1,500);
}

function startTab2(){
    document.getElementById('t2_btn').disabled=true;
    document.getElementById('t2_pbox').style.display='block';
    document.getElementById('t2_sbox').style.display='grid';
    document.getElementById('t2_rcard').style.display='none';
    let m=document.getElementById('t2_mode_sel').value;
    let r=document.getElementById('t2_ratio').value;
    fetch('/start/tab2?ratio='+r+'&mode='+m);
    t2_timer=setInterval(pollTab2,500);
}

function pollTab1(){
    fetch('/status/tab1').then(r=>r.json()).then(d=>{
        let p=d.total?Math.round(d.progress/d.total*100):0;
        document.getElementById('t1_pbar').style.width=p+'%';
        document.getElementById('t1_pbar').textContent=p+'% - '+d.current_stock;
        document.getElementById('t1_s1').textContent=d.total;
        document.getElementById('t1_s2').textContent=d.stats.success;
        document.getElementById('t1_s3').textContent=d.stats.failed;
        document.getElementById('t1_s4').textContent=d.stats.matched;
        if(d.message){document.getElementById('t1_msg').style.display='block';document.getElementById('t1_msg').textContent=d.message;}
        if(d.status=='completed'||d.status=='error'){
            clearInterval(t1_timer);
            document.getElementById('t1_btn').disabled=false;
            if(d.results.length)showTab1Results(d.results);
        }
    });
}

function pollTab2(){
    fetch('/status/tab2').then(r=>r.json()).then(d=>{
        let p=d.total?Math.round(d.progress/d.total*100):0;
        document.getElementById('t2_pbar').style.width=p+'%';
        document.getElementById('t2_pbar').textContent=p+'% - '+d.current_stock;
        document.getElementById('t2_s1').textContent=d.total;
        document.getElementById('t2_s2').textContent=d.stats.success;
        document.getElementById('t2_s3').textContent=d.stats.failed;
        document.getElementById('t2_s4').textContent=d.stats.matched;
        if(d.message){
            let msg=document.getElementById('t2_msg');
            msg.style.display='block';
            msg.textContent=d.message;
            msg.className=d.market_macd>0?'msg warning':'msg';
        }
        if(d.status=='completed'||d.status=='error'){
            clearInterval(t2_timer);
            document.getElementById('t2_btn').disabled=false;
            if(d.results.length)showTab2Results(d.results);
        }
    });
}

function showTab1Results(r){
    document.getElementById('t1_rcard').style.display='block';
    let h='';
    r.slice(0,20).forEach((x,i)=>{
        let sc=x['信号']=='金叉'?'up':(x['信号']=='绿柱缩短'?'hot':'');
        h+=`<tr><td>${i+1}</td><td><b>${x['代码']}</b></td><td>${x['名称']}</td><td>${x['收盘价']}</td><td>${x['MA55']}</td><td><b>${x['偏离度%']}%</b></td><td>${x['DEA']}</td><td>${x['历史DEA']}</td><td>${x['回落比%']}%</td><td><span class="tag ${sc}">${x['信号']}</span></td></tr>`;
    });
    document.getElementById('t1_tbody').innerHTML=h;
}

function showTab2Results(r){
    document.getElementById('t2_rcard').style.display='block';
    let h='';
    r.slice(0,20).forEach((x,i)=>{
        let sc=x['信号']=='金叉'?'up':'down';
        h+=`<tr><td>${i+1}</td><td><b>${x['代码']}</b></td><td>${x['名称']}</td><td>${x['收盘价']}</td><td>${x['市值(亿)']}亿</td><td>${x['PB']}</td><td>${x['Beta']}</td><td>${x['DEA']}</td><td><span class="tag ${sc}">${x['信号']}</span></td></tr>`;
    });
    document.getElementById('t2_tbody').innerHTML=h;
}

function exportExcel(tab){
    window.open('/export/'+tab, '_blank');
}
</script>
</body>
</html>
'''


@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/start/<tab>')
def start(tab):
    mode = request.args.get('mode', 'default')
    ratio = float(request.args.get('ratio', 0.05))
    
    if tab == 'tab1':
        golden_only = request.args.get('golden', '0') == '1'
        STATE['tab1'] = {
            'status': 'idle', 'progress': 0, 'total': 0, 'current_stock': '',
            'data_source': '自动选择', 'results': [],
            'stats': {'success': 0, 'failed': 0, 'matched': 0},
            'message': '', 'mode': mode, 'golden_only': golden_only
        }
        threading.Thread(target=run_task_tab1, args=(ratio, mode, golden_only), daemon=True).start()
    elif tab == 'tab2':
        STATE['tab2'] = {
            'status': 'idle', 'progress': 0, 'total': 0, 'current_stock': '',
            'data_source': '东方财富', 'results': [],
            'stats': {'success': 0, 'failed': 0, 'matched': 0},
            'message': '', 'mode': mode, 'market_macd': 0
        }
        threading.Thread(target=run_task_tab2, args=(ratio, mode), daemon=True).start()
    
    return jsonify({'ok': True})


@app.route('/status/<tab>')
def status(tab):
    return jsonify(STATE.get(tab, {}))


@app.route('/export/<tab>')
def export_excel(tab):
    """导出Excel文件"""
    try:
        state = STATE.get(tab, {})
        results = state.get('results', [])
        
        if not results:
            return jsonify({'error': '没有数据可导出'}), 400
        
        # 转换为DataFrame
        df = pd.DataFrame(results)
        
        # 创建Excel文件
        output = BytesIO()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # 写入数据
            df.to_excel(writer, sheet_name='选股结果', index=False)
            
            # 添加统计信息
            stats = state.get('stats', {})
            mode = state.get('mode', 'default')
            
            stats_data = {
                '统计项目': ['筛选模式', '总处理数', '成功数', '失败数', '符合条件数', '导出时间'],
                '数值': [
                    TAB1_MODES[mode]['name'] if tab == 'tab1' else TAB2_MODES[mode]['name'],
                    stats.get('success', 0) + stats.get('failed', 0),
                    stats.get('success', 0),
                    stats.get('failed', 0),
                    stats.get('matched', 0),
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                ]
            }
            
            stats_df = pd.DataFrame(stats_data)
            stats_df.to_excel(writer, sheet_name='统计信息', index=False)
        
        output.seek(0)
        
        # 生成文件名
        tab_name = "周线MACD" if tab == 'tab1' else "月线基本面"
        filename = f"选股结果_{tab_name}_{timestamp}.xlsx"
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    
    print("=" * 70)
    print("🚀 多策略选股器 - 统一版")
    print("=" * 70)
    print("Tab1: 周线 MACD 零轴回踩选股")
    print("Tab2: 月线 MACD + 基本面筛选")
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 70)
    print(f"浏览器打开: http://{host}:{port}")
    print("=" * 70)
    app.run(host=host, port=port, debug=False)
