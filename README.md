# 周线MACD零轴回踩起爆选股系统

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![AKShare](https://img.shields.io/badge/Data-AKShare-green)](https://github.com/akfamily/akshare)
[![Flask](https://img.shields.io/badge/Web-Flask-lightgrey.svg)](https://flask.palletsprojects.com/)
[![MIT License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

基于 AKShare 数据接口的 A 股量化选股系统，核心策略为**周线 MACD 零轴回踩起爆**。

## 功能特性

- **Web 界面选股**: 浏览器访问，一键筛选，实时显示进度
- **多数据源支持**: 东方财富 + 腾讯财经双数据源自动切换
- **多种筛选模式**: 严格/默认/宽松三档可调
- **技术指标计算**: MACD、MA55、MA20 等
- **智能过滤**: 自动排除 ST、退市、新股

## 选股策略

核心逻辑：寻找经历过大牛市后充分回调、目前在 MA55 均线附近企稳、MACD 即将金叉的股票。

### 筛选条件

1. **均线支撑**: 股价在 MA55 附近 (偏离度 -5% ~ +10%)
2. **大趋势回调**: 历史 DEA 高点充分，当前回调到位
3. **零轴企稳**: DEA 在零轴附近 (0 ~ 1)
4. **趋势反转**: DIF 金叉 DEA 或绿柱缩短

## 快速开始

### 安装依赖

```shell
pip install akshare flask pandas tqdm
```

### 启动 Web 选股器

```shell
python examples/stock_screener_web.py
```

访问 http://127.0.0.1:5000 即可使用。

### 命令行选股

```shell
python examples/stock_screener_monthly_macd.py
```

## 项目结构

```
├── akshare/                    # AKShare 数据接口库
├── examples/                   # 选股应用
│   ├── stock_screener_web.py           # Web版选股器 ⭐
│   ├── stock_screener_monthly_macd.py  # 命令行版
│   ├── stock_screener_unified.py       # 统一选股器
│   └── 选股系统架构说明.md              # 详细架构文档
├── scripts/                    # 启动脚本
└── docs/                       # 文档
```

## 使用示例

```python
import akshare as ak

# 获取股票周线数据
df = ak.stock_zh_a_hist(
    symbol="000001",
    period="weekly",
    start_date="20210101",
    adjust="qfq"
)
print(df)
```

## 数据来源

- [东方财富](http://data.eastmoney.com) - 主数据源
- [腾讯财经](https://finance.qq.com) - 备用数据源
- [AKShare](https://github.com/akfamily/akshare) - 数据接口封装

## 免责声明

1. 本项目仅供学习研究使用，不构成任何投资建议
2. 投资有风险，入市需谨慎
3. 历史表现不代表未来收益

## License

MIT License
