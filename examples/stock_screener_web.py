#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
周线 MACD 零轴回踩起爆选股器 - Web版
数据源: 自动选择（优先东方财富 stock_zh_a_hist；失败则切换腾讯 stock_zh_a_hist_tx 并聚合成周线）
"""

import os
import time
import threading
from datetime import datetime, timedelta
from typing import Optional

from flask import Flask, render_template_string, jsonify, request

import akshare as ak
import pandas as pd

app = Flask(__name__)

# 三种筛选模式参数 (以牧原股份002714为锚点: MA偏离1.84%, DEA=0.87, 历史DEA=3.33, 回落比26%)
MODES = {
    'strict': {  # 严格模式 - 更接近零轴
        'name': '严格模式',
        'ma_min': 0,        # MA55偏离度下限 0%
        'ma_max': 0.08,     # MA55偏离度上限 8%
        'history_dea': 0.3, # 历史DEA最低要求
        'pullback': 0.3,    # 当前DEA需小于历史的30%
        'dea_min': 0,       # DEA下限
        'dea_max': 0.5,     # DEA上限 (牧原0.87不符合)
    },
    'default': {  # 默认模式 - 以牧原股份为准
        'name': '默认模式',
        'ma_min': -0.05,    # 允许略微跌穿MA55 -5%
        'ma_max': 0.10,     # MA55偏离度上限 10% (牧原1.84%符合)
        'history_dea': 0.1, # 历史DEA最低要求 (牧原3.33符合)
        'pullback': 0.5,    # 当前DEA需小于历史的50% (牧原26%符合)
        'dea_min': 0,       # DEA下限
        'dea_max': 1.0,     # DEA上限 (牧原0.87符合)
    },
    'loose': {  # 宽松模式 - 更宽泛的筛选
        'name': '宽松模式',
        'ma_min': -0.10,    # 允许跌穿MA55 10%
        'ma_max': 0.20,     # MA55偏离度上限 20%
        'history_dea': 0.05,# 历史DEA最低要求
        'pullback': 0.7,    # 当前DEA需小于历史的70%
        'dea_min': -0.2,    # 允许负值
        'dea_max': 1.5,     # DEA上限
    }
}

# 全局状态
STATE = {
    'status': 'idle',
    'progress': 0,
    'total': 0,
    'current_stock': '',
    'data_source': '自动选择',
    'results': [],
    'stats': {'success': 0, 'failed': 0, 'matched': 0},
    'message': '',
    'mode': 'default',
    'golden_only': False  # True=仅金叉, False=包含即将金叉
}

# 数据源健康状态：如果东方财富接口在当前网络环境不可达，会自动跳过以提升整体速度
DATA_SOURCE_HEALTH = {
    "eastmoney_ok": None,  # None=未知, True=可用, False=不可用
}


def calculate_macd(df, fast=12, slow=26, signal=9):
    close = df['收盘']
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    df['DIF'] = ema_fast - ema_slow
    df['DEA'] = df['DIF'].ewm(span=signal, adjust=False).mean()
    df['MACD柱'] = 2 * (df['DIF'] - df['DEA'])
    return df


def _fetch_weekly_eastmoney(
    symbol: str, start_date: str, end_date: str
) -> Optional[pd.DataFrame]:
    """东方财富周线(直接拿周线)."""
    df = ak.stock_zh_a_hist(
        symbol=symbol,
        period="weekly",
        start_date=start_date,
        end_date=end_date,
        adjust="qfq",
        timeout=10,
    )
    if df is None or df.empty:
        return None
    return df[["日期", "开盘", "最高", "最低", "收盘", "成交量"]].copy()


def _fetch_weekly_tencent(
    symbol: str, start_date: str, end_date: str
) -> Optional[pd.DataFrame]:
    """腾讯仅提供日线，这里用日线聚合成周线(周五收盘)."""
    prefix = "sh" if symbol.startswith("6") else "sz"
    df_daily = ak.stock_zh_a_hist_tx(
        symbol=f"{prefix}{symbol}",
        start_date=start_date,
        end_date=end_date,
        adjust="qfq",
        timeout=10,
    )
    if df_daily is None or df_daily.empty:
        return None

    # 腾讯接口字段为英文: date/open/close/high/low/amount
    df_daily = df_daily.copy()
    df_daily["date"] = pd.to_datetime(df_daily["date"], errors="coerce")
    df_daily = df_daily.dropna(subset=["date"]).set_index("date").sort_index()

    # 周线聚合：以周五为周期终点（无交易日会自动用最后一个交易日）
    df_weekly = (
        df_daily.resample("W-FRI")
        .agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "amount": "sum",
        })
        .dropna()
        .reset_index()
    )

    df_weekly = df_weekly.rename(
        columns={
            "date": "日期",
            "open": "开盘",
            "high": "最高",
            "low": "最低",
            "close": "收盘",
            "amount": "成交量",
        }
    )

    # 与东方财富返回字段保持一致
    df_weekly["日期"] = pd.to_datetime(df_weekly["日期"], errors="coerce").dt.date
    return df_weekly[["日期", "开盘", "最高", "最低", "收盘", "成交量"]].copy()


def get_weekly_data(symbol, start_date, end_date):
    """获取周线数据（自动选择数据源）

    背景：某些网络环境下东方财富/新浪等接口可能被中间网络设备或目标站点直接断开连接（RemoteDisconnected），
    导致“全部周线获取失败”。为保证功能可用性，这里增加腾讯数据源兜底。
    """
    global DATA_SOURCE_HEALTH

    # 1) 优先东方财富（如果之前已经判断不可用，则直接跳过）
    if DATA_SOURCE_HEALTH.get("eastmoney_ok") is not False:
        try:
            df = _fetch_weekly_eastmoney(symbol=symbol, start_date=start_date, end_date=end_date)
            if df is not None and len(df) >= 55:
                DATA_SOURCE_HEALTH["eastmoney_ok"] = True
                STATE["data_source"] = "东方财富 (stock_zh_a_hist)"
                return df
        except Exception as e:
            # 一旦确认不可达，后续直接跳过东方财富，避免每只股票都白白重试
            DATA_SOURCE_HEALTH["eastmoney_ok"] = False
            STATE["message"] = f"东方财富接口不可用，已自动切换到腾讯数据源。错误: {type(e).__name__}"

    # 2) 腾讯兜底（用日线聚合成周线）
    for retry in range(2):
        try:
            df = _fetch_weekly_tencent(symbol=symbol, start_date=start_date, end_date=end_date)
            if df is not None and len(df) >= 55:
                STATE["data_source"] = "腾讯 (stock_zh_a_hist_tx → 周线聚合)"
                return df
        except Exception:
            if retry < 1:
                time.sleep(0.5)

    return None


def screen_stock(symbol, name, end_date, mode='default', golden_only=False):
    """筛选单只股票 - 支持多种模式"""
    params = MODES[mode]
    
    try:
        start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=3*365)).strftime("%Y%m%d")
        df = get_weekly_data(symbol, start_date, end_date)
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
        
        # 条件1: 均线支撑
        deviation = (close - ma55) / ma55
        if deviation < params['ma_min']: return None, 'below_ma'
        if deviation >= params['ma_max']: return None, 'too_far'
        
        # 条件2: 大趋势回调
        max_dea_100w = df.tail(100)['DEA'].max()
        if max_dea_100w <= params['history_dea']: return None, 'low_history'
        if dea >= params['pullback'] * max_dea_100w: return None, 'no_pullback'
        
        # 条件3: 零轴企稳
        if dea <= params['dea_min'] or dea >= params['dea_max']: return None, 'dea_range'
        
        # 条件4: 趋势反转
        is_golden = dif > dea
        is_green_shrink = macd > prev_macd and macd < 0
        
        if is_golden:
            signal = "金叉"
        elif is_green_shrink:
            signal = "绿柱缩短"
        else:
            signal = "待金叉"
        
        # 根据开关筛选
        if golden_only and not is_golden:
            return None, 'no_golden'  # 开关打开：仅要已金叉
        # 开关关闭：包含所有（已金叉+即将金叉）
        
        # MACD柱状态
        if macd > 0 and prev_macd <= 0: macd_status = "翻红"
        elif macd > prev_macd: macd_status = "红柱放大" if macd > 0 else "绿柱缩短"
        else: macd_status = "红柱缩短" if macd > 0 else "绿柱放大"
        
        return {
            '代码': symbol, '名称': name, '收盘价': round(close, 2),
            'MA55': round(ma55, 2), '偏离度%': round(deviation * 100, 2),
            'DEA': round(dea, 4), 'DIF': round(dif, 4),
            '历史DEA': round(max_dea_100w, 4),
            '回落比%': round((1 - dea / max_dea_100w) * 100, 1),
            '信号': signal,
            'MACD': macd_status
        }, 'matched'
    except:
        return None, 'error'


def run_task(ratio):
    global STATE
    STATE.update({'status': 'running', 'progress': 0, 'results': [], 
                  'stats': {'success': 0, 'failed': 0, 'matched': 0}, 'message': '获取股票列表...'})
    
    try:
        stocks = ak.stock_info_a_code_name()
        stocks.columns = ['代码', '名称']
        stocks = stocks[~stocks['名称'].str.contains('ST|退', na=False)]
        stocks = stocks[stocks['代码'].str.match(r'^(00|30|60|68)')]
        if ratio < 1.0:
            stocks = stocks.sample(frac=ratio, random_state=42)
        
        STATE['total'] = len(stocks)
        STATE['message'] = f'筛选 {len(stocks)} 只股票...'
        end_date = datetime.now().strftime("%Y%m%d")
        
        for i, (_, row) in enumerate(stocks.iterrows()):
            STATE['progress'] = i + 1
            STATE['current_stock'] = f"{row['代码']} {row['名称']}"
            
            result, status = screen_stock(row['代码'], row['名称'], end_date, STATE['mode'], STATE['golden_only'])
            if result:
                STATE['results'].append(result)
                STATE['stats']['matched'] += 1
                STATE['stats']['success'] += 1
            elif status in ['no_data', 'error']:
                STATE['stats']['failed'] += 1
            else:
                STATE['stats']['success'] += 1
            time.sleep(0.1)
        
        STATE['results'] = sorted(STATE['results'], key=lambda x: x['偏离度%'])
        STATE['status'] = 'completed'
        STATE['message'] = f'完成! 找到 {len(STATE["results"])} 只股票'
    except Exception as e:
        STATE['status'] = 'error'
        STATE['message'] = str(e)


HTML = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>周线MACD选股器</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{font-family:system-ui,sans-serif;background:#f0f2f5;padding:20px}
        .container{max-width:1200px;margin:0 auto}
        .header{background:linear-gradient(135deg,#1a73e8,#6c5ce7);color:#fff;padding:25px;border-radius:12px;margin-bottom:20px}
        .header h1{font-size:22px;margin-bottom:8px}
        .badge{background:rgba(255,255,255,.2);padding:4px 12px;border-radius:15px;font-size:13px}
        .card{background:#fff;border-radius:12px;padding:20px;margin-bottom:20px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
        .card h2{font-size:15px;color:#333;margin-bottom:12px;padding-bottom:8px;border-bottom:2px solid #1a73e8}
        .conditions{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
        .cond{background:#f8f9fa;padding:10px;border-radius:6px;font-size:13px;border-left:3px solid #1a73e8}
        .btn{background:linear-gradient(135deg,#1a73e8,#6c5ce7);color:#fff;border:none;padding:12px 28px;border-radius:8px;cursor:pointer;font-size:15px}
        .btn:disabled{background:#ccc}
        select{padding:10px;border-radius:8px;border:1px solid #ddd;margin-right:12px}
        .progress{height:22px;background:#e9ecef;border-radius:11px;overflow:hidden;margin:15px 0}
        .progress-bar{height:100%;background:linear-gradient(90deg,#1a73e8,#6c5ce7);transition:width .3s;color:#fff;font-size:12px;display:flex;align-items:center;justify-content:center}
        .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:15px 0}
        .stat{text-align:center;padding:12px;background:#f8f9fa;border-radius:8px}
        .stat-val{font-size:26px;font-weight:700;color:#1a73e8}
        .stat-lbl{font-size:11px;color:#666;margin-top:4px}
        .msg{padding:12px;background:#e3f2fd;border-radius:8px;color:#1565c0;margin:10px 0;font-size:14px}
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
        <h1>📈 周线 MACD 零轴回踩起爆选股器</h1>
        <span class="badge" id="dataSource">数据源: 自动选择</span>
    </div>
    <div class="card">
        <h2>选股条件（周线） - <span id="modeLabel">默认模式</span></h2>
        <div class="conditions" id="condBox">
            <div class="cond">1️⃣ 均线支撑: <span id="c1">-5%~+15%</span></div>
            <div class="cond">2️⃣ 历史高度: DEA最高 > <span id="c2">0.1</span></div>
            <div class="cond">3️⃣ 充分回调: 当前DEA < <span id="c3">60%</span>历史最高</div>
            <div class="cond">4️⃣ 零轴企稳: DEA在 <span id="c4">0~0.5</span></div>
            <div class="cond" id="c5box">5️⃣ 趋势反转: <span id="c5">包含即将金叉</span></div>
        </div>
    </div>
    <div class="card">
        <h2>控制面板</h2>
        <select id="mode" onchange="updateMode()">
            <option value="strict">🔒 严格模式</option>
            <option value="default" selected>✅ 默认模式</option>
            <option value="loose">📦 宽松模式</option>
        </select>
        <label style="display:inline-flex;align-items:center;margin-right:12px;cursor:pointer">
            <input type="checkbox" id="goldenOnly" onchange="updateGolden()" style="width:18px;height:18px;margin-right:6px">
            <span>✨ 仅已金叉</span>
        </label>
        <select id="ratio">
            <option value="0.01">1% (~50只)</option>
            <option value="0.05" selected>5% (~250只)</option>
            <option value="0.10">10% (~500只)</option>
            <option value="0.30">30% (~1500只)</option>
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
        <h2>筛选结果 (按MA55偏离度排序)</h2>
        <table><thead><tr><th>#</th><th>代码</th><th>名称</th><th>收盘</th><th>MA55</th><th>偏离度</th><th>DEA</th><th>历史DEA</th><th>回落比</th><th>信号</th></tr></thead>
        <tbody id="tbody"></tbody></table>
    </div>
</div>
<script>
let timer;
const MODES={
    strict:{name:'严格模式',c1:'0%~+8%',c2:'0.3',c3:'40%',c4:'0~0.15'},
    default:{name:'默认模式',c1:'-5%~+15%',c2:'0.1',c3:'60%',c4:'0~0.5'},
    loose:{name:'宽松模式',c1:'-10%~+25%',c2:'0.05',c3:'80%',c4:'-0.1~1.0'}
};
function updateMode(){
    let m=document.getElementById('mode').value;
    let p=MODES[m];
    document.getElementById('modeLabel').textContent=p.name;
    document.getElementById('c1').textContent=p.c1;
    document.getElementById('c2').textContent=p.c2;
    document.getElementById('c3').textContent=p.c3;
    document.getElementById('c4').textContent=p.c4;
}
function updateGolden(){
    let checked=document.getElementById('goldenOnly').checked;
    document.getElementById('c5').textContent=checked?'仅已金叉':'包含即将金叉';
}
function start(){
    document.getElementById('btn').disabled=true;
    document.getElementById('pbox').style.display='block';
    document.getElementById('sbox').style.display='grid';
    document.getElementById('rcard').style.display='none';
    let m=document.getElementById('mode').value;
    let r=document.getElementById('ratio').value;
    let g=document.getElementById('goldenOnly').checked?'1':'0';
    fetch('/start?ratio='+r+'&mode='+m+'&golden='+g);
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
        let ds=document.getElementById('dataSource');
        if(ds && d.data_source){ds.textContent='数据源: '+d.data_source;}
        if(d.message){document.getElementById('msg').style.display='block';document.getElementById('msg').textContent=d.message;}
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
    r.slice(0,20).forEach((x,i)=>{
        let sc=x['信号']=='金叉'?'up':(x['信号']=='绿柱缩短'?'hot':'');
        h+=`<tr><td>${i+1}</td><td><b>${x['代码']}</b></td><td>${x['名称']}</td><td>${x['收盘价']}</td><td>${x['MA55']}</td><td><b>${x['偏离度%']}%</b></td><td>${x['DEA']}</td><td>${x['历史DEA']}</td><td>${x['回落比%']}%</td><td><span class="tag ${sc}">${x['信号']}</span></td></tr>`;
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
    golden_only = request.args.get('golden', '0') == '1'
    STATE = {'status': 'idle', 'progress': 0, 'total': 0, 'current_stock': '',
             'data_source': '自动选择', 'results': [], 'stats': {'success': 0, 'failed': 0, 'matched': 0}, 
             'message': '', 'mode': mode, 'golden_only': golden_only}
    ratio = float(request.args.get('ratio', 0.05))
    threading.Thread(target=run_task, args=(ratio,), daemon=True).start()
    return jsonify({'ok': True})

@app.route('/status')
def status():
    return jsonify(STATE)

if __name__ == '__main__':
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))

    print("=" * 50)
    print("周线 MACD 选股器 - Web版")
    print("=" * 50)
    print("数据源: 自动选择（优先东方财富；失败则切换腾讯并聚合周线）")
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 50)
    if host in {"0.0.0.0", "::"}:
        print(f"浏览器打开: http://127.0.0.1:{port} (服务器本机) 或 http://<服务器IP>:{port}")
    else:
        print(f"浏览器打开: http://{host}:{port}")
    print("=" * 50)
    app.run(host=host, port=port)
