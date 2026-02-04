# AGENTS.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

AKShare is a Python library for fetching financial data from various sources (stocks, futures, options, funds, bonds, forex, crypto). Data interfaces return pandas DataFrames with Chinese column names from sources like 东方财富 (EastMoney), 新浪财经, 金十数据, etc.

**Language**: Python 3.8+
**Documentation (Chinese)**: https://akshare.akfamily.xyz/

## Commands

### Installation
```shell
pip install akshare --upgrade
# or with dev dependencies:
pip install -e ".[dev]"
```

### Linting and Formatting (Ruff)
```shell
# Lint and auto-fix
ruff check --fix akshare/

# Format code
ruff format akshare/
```

### Pre-commit hooks
```shell
# Install pre-commit hooks
pre-commit install

# Run all hooks on all files
pre-commit run --all-files
```

### Testing
```shell
# Run tests (pytest is NOT explicitly configured - tests are minimal)
python -m pytest tests/

# Run a single test file
python tests/test_func.py
```

### Building
```shell
pip install build
python -m build
```

## Architecture

### Package Structure
- `akshare/` - Main package root
  - `__init__.py` - Exposes all public APIs (imports from submodules)
  - `_version.py` - Single source of version number
  - `request.py` - HTTP request helpers with retry logic
  - `exceptions.py` - Custom exception hierarchy (`AkshareException` base)
  - `datasets.py` - Utilities for accessing bundled data files
  - `utils/` - Shared utilities (request helpers, context/config, tqdm wrappers)

### Data Interface Modules
Each subdirectory contains data fetching interfaces for a specific domain:
- `stock/`, `stock_feature/`, `stock_fundamental/` - A-share, HK, US stock data
- `futures/`, `futures_derivative/` - Futures market data and derivatives
- `fund/` - Fund/ETF data
- `bond/` - Bond market data
- `option/` - Options data
- `index/` - Index data
- `economic/` - Macroeconomic indicators
- `crypto/` - Cryptocurrency data
- `forex/`, `currency/`, `fx/` - Foreign exchange data
- `energy/` - Energy/carbon market data

### Interface Pattern
Each data interface function:
1. Makes HTTP requests to external APIs (often with special params/headers)
2. Parses response (JSON/HTML/Excel) into a pandas DataFrame
3. Renames columns to Chinese names matching the data source
4. Converts column types using `pd.to_numeric(..., errors="coerce")`
5. Returns a `pd.DataFrame`

Example function signature:
```python
def stock_zh_a_hist(
    symbol: str = "000001",
    period: str = "daily",
    start_date: str = "19700101",
    end_date: str = "20500101",
    adjust: str = "",
) -> pd.DataFrame:
```

### Exception Hierarchy
```python
AkshareException (base)
├── APIError          # API request failures
├── DataParsingError  # Response parsing failures
├── InvalidParameterError
├── NetworkError      # Connection issues
└── RateLimitError    # HTTP 429
```

### Request Utilities
Use `make_request_with_retry_json()` or `make_request_with_retry_text()` from `akshare/request.py` for HTTP requests with automatic retry and exponential backoff.

## 回复规范

后续回复请尽量使用中文，并在回答中清晰说明你做了什么、为什么这么做以及可能的影响，帮助用户做出更好的决策。

- **请使用中文回复**：本项目面向中文用户，回复时请尽量使用中文
- **解释你的操作**：让用户清楚了解你做了什么、为什么这么做，帮助用户做出更好的决策
- **说明改动影响**：修改代码时，简要说明改动可能带来的影响

## Code Style

- Line length: 88 characters (Black-compatible)
- Use double quotes for strings
- Follow existing naming: `{domain}_{source}_{action}` (e.g., `stock_zh_a_hist`, `fund_etf_fund_daily_em`)
- Docstrings should include data source URL, `:param:` and `:return:` sections
- Use `pd.to_numeric(..., errors="coerce")` for numeric column conversion
- Column names should match the Chinese names from the data source website
- Exclude `akshare/__init__.py` from Ruff (handled manually for exports)
