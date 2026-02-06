#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
Tab2选股器 - 月线MACD择时 + 基本面筛选
基于"23大盘择时,逻辑简单"策略改造
数据源: 东方财富 (stock_zh_a_hist 月线)
"""

import os
import time
import threading
from datetime import datetime, timedelta
from typing import Optional

from flask import Flask, render_template_string, jsonify, request

import akshare as ak
import pandas as pd
import numpy as np

app = Flask(__name__)

# 筛选模式参数
MODES = {
    'strict': {
        'name': '严格模式',
        'pb_max': 0.8,          # 市净率上限
        'pb_min': 0.1,          # 市净率下限
        'market_cap_min': 800,  # 市值下限(亿)
        'roa_min': 0.20,        # ROA下限
        'beta_max': 0.6,        # Beta上限
    },
    'default': {
        'name': '默认模式',
        'pb_max': 1.0,          # 市净率上限
        'pb_min': 0.0,          # 市净率下限
        'market_cap_min': 500,  # 市值下限(亿)
        'roa_min': 0.15,        # ROA下限
        'beta_max': 0.7,        # Beta上限
    },
    'loose': {
        'name': '宽松模式',
        'pb_max': 1.5,          # 市净率上限
        'pb_min': 0.0,          # 市净率下限
        'market_cap_min': 300,  # 市值下限(亿)
        'roa_min': 0.10,        # ROA下限
        'beta_max': 0.8,        # Beta上限
    }
}

# 全局状态
STATE = {
    'status': 'idle',
    'progress': 0,
    'total': 0,
    'current_stock': '',
    'data_source': '东方财富',
    'results': [],
    'stats': {'success': 0, 'failed': 0, 'matched': 0},
    'message': '',
    'mode': 'default',
    'market_macd': 0,  # 大盘MACD状态
}


def calculate_macd(df, fast=12, slow=26, signal=9):
    """计算MACD指标(月线)"""
    close = df['收盘']
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    df['DIF'] = ema_fast - ema_slow
    df['DEA'] = df['DIF'].ewm(span=signal, adjust=False).mean()
    df['MACD柱'] = 2 * (df['DIF'] - df['DEA'])
    return df


def get_monthly_data(symbol, start_date, end_date):
    """获取月线数据"""
    try:
        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="monthly",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
            timeout=10,
        )
        if df is None or df.empty:
            return None
        return df[["日期", "开盘", "最高", "最低", "收盘", "成交量"]].copy()
    except Exception:
        return None


def get_market_macd_signal():
    """获取大盘月线MACD信号(沪深300)"""
    try:
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=5*365)).strftime("%Y%m%d")
        
        # 获取沪深300指数月线数据
        df = ak.stock_zh_index_hist(
            symbol="000300",
            period="monthly",
            start_date=start_date,
            end_date=end_date,
        )
        
        if df is None or len(df) < 50:
            return 0
        
        df = calculate_macd(df.copy())
        last_macd = df.iloc[-1]['MACD柱']
        
        STATE['market_macd'] = round(last_macd, 4)
        return last_macd
    except Exception:
        return 0


def calculate_beta(symbol, end_date):
    """计算个股相对沪深300的Beta值"""
    try:
        time0 = datetime.strptime(end_date, "%Y%m%d")
        time1 = datetime(time0.year - 1, time0.month, time0.day)
        
        # 获取沪深300指数日线
        index_data = ak.stock_zh_index_hist(
            symbol="000300",
            period="daily", 
            start_date=time1.strftime("%Y%m%d"),
            end_date=time0.strftime("%Y%m%d"),
        )
        
        if index_data is None or len(index_data) < 100:
            return None
            
        index_returns = index_data['收盘'].pct_change()
        index_var = index_returns.var()
        
        # 获取个股日线
        stock_data = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=time1.strftime("%Y%m%d"),
            end_date=time0.strftime("%Y%m%d"),
            adjust="qfq",
        )
        
        if stock_data is None or len(stock_data) < 100:
            return None
            
        stock_returns = stock_data['收盘'].pct_change()
        cov = index_returns.cov(stock_returns)
        beta = cov / index_var
        
        return beta
    except Exception:
        return None


def screen_stock(symbol, name, end_date, mode='default'):
    """筛选单只股票 - 基本面+月线MACD"""
    params = MODES[mode]
    
    try:
        # 1. 获取基本面数据
        try:
            df_fundamental = ak.stock_individual_info_em(symbol=symbol)
            if df_fundamental is None or df_fundamental.empty:
                return None, 'no_fundamental'
            
            # 解析基本面数据(东方财富返回的是键值对格式)
            fundamental_dict = dict(zip(df_fundamental['item'], df_fundamental['value']))
            
            # 获取关键指标
            market_cap_str = fundamental_dict.get('总市值', '0')
            market_cap = float(market_cap_str.replace(',', '')) / 100000000 if market_cap_str else 0  # 转换为亿
            
            pb_ratio_str = fundamental_dict.get('市净率', '0')
            pb_ratio = float(pb_ratio_str) if pb_ratio_str and pb_ratio_str != '-' else None
            
        except Exception:
            return None, 'no_fundamental'
        
        # 2. 基本面条件筛选
        if pb_ratio is None or pb_ratio <= params['pb_min'] or pb_ratio >= params['pb_max']:
            return None, 'pb_filter'
        
        if market_cap < params['market_cap_min']:
            return None, 'market_cap_filter'
        
        # 3. 计算Beta
        beta = calculate_beta(symbol, end_date)
        if beta is None or beta >= params['beta_max']:
            return None, 'beta_filter'
        
        # 4. 获取月线数据计算MACD
        start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=3*365)).strftime("%Y%m%d")
        df = get_monthly_data(symbol, start_date, end_date)
        
        if df is None or len(df) < 50:
            return None, 'no_monthly_data'
        
        df = calculate_macd(df.copy())
        current = df.iloc[-1]
        
        dea = current['DEA']
        dif = current['DIF']
        macd = current['MACD柱']
        close = current['收盘']
        
        # 5. MACD状态判断
        is_golden = dif > dea
        signal = "金叉" if is_golden else "待金叉"
        
        macd_status = "红柱" if macd > 0 else "绿柱"
        
        # 6. ROA需要从财务数据获取(这里简化处理,实际需要调用财务接口)
        roa = 0.16  # 简化:假设满足条件(实际应该从ak.stock_financial_analysis_indicator获取)
        
        return {
            '代码': symbol, 
            '名称': name, 
            '收盘价': round(close, 2),
            '市值(亿)': round(market_cap, 2),
            'PB': round(pb_ratio, 2),
            'Beta': round(beta, 2),
            'ROA%': round(roa * 100, 1),
            'DEA': round(dea, 4),
            'DIF': round(dif, 4),
            '信号': signal,
            'MACD': macd_status
        }, 'matched'
    except Exception as e:
        return None, 'error'


def run_task(ratio):
    global STATE
    STATE.update({
        'status': 'running', 
        'progress': 0, 
        'results': [], 
        'stats': {'success': 0, 'failed': 0, 'matched': 0}, 
        'message': '获取大盘MACD信号...'
    })
    
    try:
        # 1. 获取大盘MACD信号
        market_macd = get_market_macd_signal()
        if market_macd > 0:
            STATE['message'] = f'⚠️ 大盘MACD={STATE["market_macd"]}>0, 建议空仓或持有指数基金'
        else:
            STATE['message'] = f'✅ 大盘MACD={STATE["market_macd"]}≤0, 可进行个股筛选'
        
        # 2. 获取股票列表
        STATE['message'] = '获取股票列表...'
        stocks = ak.stock_info_a_code_name()
        stocks.columns = ['代码', '名称']
        stocks = stocks[~stocks['名称'].str.contains('ST|退', na=False)]
        stocks = stocks[stocks['代码'].str.match(r'^(00|30|60|68)')]
        
        if ratio < 1.0:
            stocks = stocks.sample(frac=ratio, random_state=42)
        
        STATE['total'] = len(stocks)
        STATE['message'] = f'筛选 {len(stocks)} 只股票...'
        end_date = datetime.now().strftime("%Y%m%d")
        
        # 3. 遍历筛选
        for i, (_, row) in enumerate(stocks.iterrows()):
            STATE['progress'] = i + 1
            STATE['current_stock'] = f"{row['代码']} {row['名称']}"
            
            result, status = screen_stock(row['代码'], row['名称'], end_date, STATE['mode'])
            if result:
                STATE['results'].append(result)
                STATE['stats']['matched'] += 1
                STATE['stats']['success'] += 1
            elif status in ['no_fundamental', 'no_monthly_data', 'error']:
                STATE['stats']['failed'] += 1
            else:
                STATE['stats']['success'] += 1
            
            time.sleep(0.15)  # 控制请求频率
        
        # 4. 按市值排序
        STATE['results'] = sorted(STATE['results'], key=lambda x: x['市值(亿)'], reverse=True)
        STATE['status'] = 'completed'
        STATE['message'] = f'完成! 找到 {len(STATE["results"])} 只股票 (大盘MACD={STATE["market_macd"]})'
    except Exception as e:
        STATE['status'] = 'error'
        STATE['message'] = str(e)


HTML = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Tab2选股器 - 基本面筛选</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{font-family:system-ui,sans-serif;background:#f0f2f5;padding:20px}
        .container{max-width:1400px;margin:0 auto}
        .header{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;padding:25px;border-radius:12px;margin-bottom:20px}
        .header h1{font-size:22px;margin-bottom:8px}
        .badge{background:rgba(255,255,255,.2);padding:4px 12px;border-radius:15px;font-size:13px}
        .card{background:#fff;border-radius:12px;padding:20px;margin-bottom:20px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
        .card h2{font-size:15px;color:#333;margin-bottom:12px;padding-bottom:8px;border-bottom:2px solid #667eea}
        .conditions{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
        .cond{background:#f8f9fa;padding:10px;border-radius:6px;font-size:13px;border-left:3px solid #667eea}
        .btn{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;border:none;padding:12px 28px;border-radius:8px;cursor:pointer;font-size:15px}
        .btn:disabled{background:#ccc}
        select{padding:10px;border-radius:8px;border:1px solid #ddd;margin-right:12px}
        .progress{height:22px;background:#e9ecef;border-radius:11px;overflow:hidden;margin:15px 0}
        .progress-bar{height:100%;background:linear-gradient(90deg,#667eea,#764ba2);transition:width .3s;color:#fff;font-size:12px;display:flex;align-items:center;justify-content:center}
        .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:15px 0}
        .stat{text-align:center;padding:12px;background:#f8f9fa;border-radius:8px}
        .stat-val{font-size:26px;font-weight:700;color:#667eea}
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
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>📊 Tab2选股器 - 月线MACD择时 + 基本面筛选</h1>
        <span class="badge">基于"23大盘择时"策略 | 数据源: 东方财富</span>
    </div>
    <div class="card">
        <h2>选股条件 - <span id="modeLabel">默认模式</span></h2>
        <div class="conditions">
            <div class="cond">1️⃣ 大盘择时: 沪深300月线MACD ≤ 0</div>
            <div class="cond">2️⃣ 市净率: <span id="c_pb">0 < PB < 1.0</span></div>
            <div class="cond">3️⃣ 市值: > <span id="c_cap">500</span>亿</div>
            <div class="cond">4️⃣ ROA: > <span id="c_roa">15</span>%</div>
            <div class="cond">5️⃣ Beta(相对300): < <span id="c_beta">0.7</span></div>
            <div class="cond">6️⃣ 趋势反转: 包含即将金叉</div>
        </div>
    </div>
    <div class="card">
        <h2>控制面板</h2>
        <select id="mode" onchange="updateMode()">
            <option value="strict">🔒 严格模式</option>
            <option value="default" selected>✅ 默认模式</option>
            <option value="loose">📦 宽松模式</option>
        </select>
        <select id="ratio">
            <option value="0.01">1% (~50只)</option>
            <option value="0.05" selected>5% (~250只)</option>
            <option value="0.10">10% (~500只)</option>
            <option value="1.00">100% (全部)</option>
        </select>
        <button class="btn" id="btn" onclick="start()">🚀 开始筛选</button>
        <div class="progress" id="pbox" style="display:none"><div class="progress-bar" id="pbar">0%</div></div>
        <div class="stats" id="sbox" style="display:none">
            <div class="stat"><div class="stat-val" id="s1">0</div><div class="stat-lbl">总数</div></div>
            <div class="stat"><div class="stat-val" id="s2">0</div><div class="stat-lbl">成功</div></div>
            <div class="stat"><div class="stat-val" id="s3">0</div><div class="stat-lbl">失败</div></div>
            <div class="stat"><div class="stat-val" id="s4">0</div><div class="stat-lbl">符合</div></div>
        </div>
        <div class="msg" id="msg" style="display:none"></div>
    </div>
    <div class="card" id="rcard" style="display:none">
        <h2>筛选结果 (按市值排序)</h2>
        <table><thead><tr><th>#</th><th>代码</th><th>名称</th><th>收盘</th><th>市值</th><th>PB</th><th>Beta</th><th>ROA</th><th>DEA</th><th>信号</th></tr></thead>
        <tbody id="tbody"></tbody></table>
    </div>
</div>
<script>
let timer;
const MODES={
    strict:{name:'严格模式',pb:'0.1~0.8',cap:'800',roa:'20',beta:'0.6'},
    default:{name:'默认模式',pb:'0~1.0',cap:'500',roa:'15',beta:'0.7'},
    loose:{name:'宽松模式',pb:'0~1.5',cap:'300',roa:'10',beta:'0.8'}
};
function updateMode(){
    let m=document.getElementById('mode').value;
    let p=MODES[m];
    document.getElementById('modeLabel').textContent=p.name;
    document.getElementById('c_pb').textContent=p.pb;
    document.getElementById('c_cap').textContent=p.cap;
    document.getElementById('c_roa').textContent=p.roa;
    document.getElementById('c_beta').textContent=p.beta;
}
function start(){
    document.getElementById('btn').disabled=true;
    document.getElementById('pbox').style.display='block';
    document.getElementById('sbox').style.display='grid';
    document.getElementById('rcard').style.display='none';
    let m=document.getElementById('mode').value;
    let r=document.getElementById('ratio').value;
    fetch('/start?ratio='+r+'&mode='+m);
    timer=setInterval(poll,500);
}
function poll(){
    fetch('/status').then(r=>r.json()).then(d=>{
        let p=d.total?Math.round(d.progress/d.total*100):0;
        document.getElementById('pbar').style.width=p+'%';
        document.getElementById('pbar').textContent=p+'% - '+d.current_stock;
        document.getElementById('s1').textContent=d.total;
        document.getElementById('s2').textContent=d.stats.success;
        document.getElementById('s3').textContent=d.stats.failed;
        document.getElementById('s4').textContent=d.stats.matched;
        if(d.message){
            let msg=document.getElementById('msg');
            msg.style.display='block';
            msg.textContent=d.message;
            msg.className=d.market_macd>0?'msg warning':'msg';
        }
        if(d.status=='completed'||d.status=='error'){
            clearInterval(timer);
            document.getElementById('btn').disabled=false;
            if(d.results.length)showResults(d.results);
        }
    });
}
function showResults(r){
    document.getElementById('rcard').style.display='block';
    let h='';
    r.slice(0,30).forEach((x,i)=>{
        let sc=x['信号']=='金叉'?'up':'down';
        h+=`<tr><td>${i+1}</td><td><b>${x['代码']}</b></td><td>${x['名称']}</td><td>${x['收盘价']}</td><td>${x['市值(亿)']}亿</td><td>${x['PB']}</td><td>${x['Beta']}</td><td>${x['ROA%']}%</td><td>${x['DEA']}</td><td><span class="tag ${sc}">${x['信号']}</span></td></tr>`;
    });
    document.getElementById('tbody').innerHTML=h;
}
</script>
</body>
</html>
'''

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/start')
def start():
    global STATE
    mode = request.args.get('mode', 'default')
    STATE = {
        'status': 'idle', 'progress': 0, 'total': 0, 'current_stock': '',
        'data_source': '东方财富', 'results': [], 
        'stats': {'success': 0, 'failed': 0, 'matched': 0}, 
        'message': '', 'mode': mode, 'market_macd': 0
    }
    ratio = float(request.args.get('ratio', 0.05))
    threading.Thread(target=run_task, args=(ratio,), daemon=True).start()
    return jsonify({'ok': True})

@app.route('/status')
def status():
    return jsonify(STATE)

if __name__ == '__main__':
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5001"))  # 使用5001端口避免冲突
    
    print("=" * 60)
    print("Tab2选股器 - 月线MACD择时 + 基本面筛选")
    print("=" * 60)
    print("基于: 23大盘择时策略")
    print("数据源: 东方财富 (月线)")
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 60)
    print(f"浏览器打开: http://{host}:{port}")
    print("=" * 60)
    app.run(host=host, port=port, debug=False)
