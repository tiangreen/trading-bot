# trading-bot

低频、只做多、不加杠杆的 **趋势过滤 + 波动率目标** 交易机器人，面向在德国/欧盟的个人投资者：

| 资产 | 券商/交易所 | 说明 |
|---|---|---|
| 美股 | **Interactive Brokers**（IBKR Ireland / Central Europe） | 通过 TWS / IB Gateway API，`ib_async` |
| 加密货币 | **Kraken**（欧盟 MiCA 牌照，EUR 交易对） | 通过 `ccxt` |

> ⚠️ 本项目仅供学习研究，不构成投资建议。任何策略都可能亏损。请先回测、再模拟盘、最后才用小资金实盘。

## 策略：趋势过滤 + 波动率目标

每天收盘后，对每个“仓位组”（`stocks` / `crypto`）计算：

1. **趋势过滤**：收盘价 > 200 日均线才持有，否则该资产权重为 0（持现金）。
2. **反波动率加权**：在趋势向上的资产中，波动越低权重越高。
3. **组合波动率目标**：用 60 日协方差估算组合波动率，整体缩放到目标（股票 12%、加密 25% 年化），总仓位 ≤ 100%（无杠杆）。
4. **单资产上限**：股票每只 ≤ 20%，加密每个 ≤ 50%。

执行规则：

- **退出随时执行**：任何一天跌破均线 → 当天卖出（控制回撤）。
- **加仓/调仓每周一次**：每周首个交易日，且仅当偏离目标超过 5% 时才交易（降低手续费与税务事件）。

为什么“稳健”：它不预测价格，只在上升趋势中持有，在熊市里大部分时间是现金；波动率目标让仓位随市场风险自动缩放。代价是：震荡市会有来回打脸的小亏损，牛市中通常跑输满仓持有。目标是 **更小的最大回撤**，而不是最高收益。

## 架构

```
tradingbot/
  strategy.py     目标权重（纯函数，无未来函数，有测试）
  rebalance.py    权重 → 订单（回测与实盘共用同一套规则）
  risk.py         数据新鲜度检查、单笔上限、订单数上限、回撤熔断
  runner.py       单次运行：读行情 → 读真实持仓 → 风控 → 下单 → 记录
  backtest.py     日频回测（计手续费+滑点），与实盘规则一致
  state.py        state/state.json（净值峰值、熔断状态）+ state/trades.csv
  brokers/        ibkr.py, kraken.py, paper.py（测试用）
```

设计原则：**持仓永远以券商返回为准**，程序重启/崩溃不会导致状态错乱；默认 dry-run，必须显式加 `--live` 才会下单。

## 安装

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp .env.example .env          # 填入 Kraken API Key
pytest -q                     # 运行测试
```

## 1. 回测

```bash
python -m tradingbot backtest --sleeve stocks --start 2012-01-01 --plot stocks.png
python -m tradingbot backtest --sleeve crypto --start 2017-01-01 --plot crypto.png
python -m tradingbot backtest --sleeve stocks --synthetic   # 离线随机数据演示
```

数据来自 Yahoo Finance（缓存在 `data/`）。看 **最大回撤、夏普、Calmar**，并与等权买入持有对比。注意：默认股票池是“今天的大公司”，回测存在幸存者偏差，结果会偏乐观。

## 仪表盘（Dashboard）

```bash
python -m tradingbot dashboard --synthetic          # 离线随机数据演示
python -m tradingbot dashboard --start 2015-01-01   # Yahoo 历史数据，所有仓位组
```

生成单文件 `dashboard.html`（浏览器直接打开）。所有数字都来自本项目：回测（与实盘同一套规则）、`state/state.json`（熔断状态、净值峰值）和 `state/trades.csv`（真实/dry-run 订单）。中间的环形图：转一圈 = 整个回测期，每条线 = 某资产的 价格/均线（20/50/100/200 日）对数比，颜色 = 当时的仓位水平，向外的细线 = 仓位，向内的红线 = 回撤，红点 = 最深的回撤谷底；鼠标悬停可查看任意一周的权益与持仓。

## 2. 模拟盘（强烈建议跑 ≥ 1 个月）

**IBKR**：在账户管理里开通 Paper Trading 账户 → 启动 TWS 或 IB Gateway，登录模拟账户 → 设置 *API → Settings*：勾选 *Enable ActiveX and Socket Clients*，端口 7497（TWS 模拟）/ 4002（Gateway 模拟），可勾选 *Read-Only API* 先测试读取。在模拟账户里准备 USD 现金。

**Kraken**：没有现货测试网，用 dry-run 代替（读取真实行情和余额，但不下单）：

```bash
python -m tradingbot run                  # 所有仓位组 dry-run，打印目标仓位和拟下订单
python -m tradingbot run --sleeve stocks --live   # IBKR 模拟账户上真实下单
```

## 3. 实盘

- IBKR 端口改为 7496 / 4001（实盘），`max_capital` 先设小金额。
- Kraken API Key 只开 *Query Funds / Query Orders & Trades / Create & Modify Orders*，**绝不开提现**，并设置 IP 白名单。建议为机器人单独使用一个子账户（机器人会把账户里所有 BTC/ETH 视为它的仓位）。
- 用 cron 每天运行一次（德国时间示例，`crontab -e`）：

```cron
# 加密：每天 02:15 （UTC 日线已收盘）
15 2 * * *   cd /path/to/trading-bot && .venv/bin/python -m tradingbot run --sleeve crypto --live
# 美股：周一至周五 16:15，美股开盘（15:30 CET）后 45 分钟
15 16 * * 1-5 cd /path/to/trading-bot && .venv/bin/python -m tradingbot run --sleeve stocks --live
```

- 查看状态 / 解除熔断：

```bash
python -m tradingbot status
python -m tradingbot reset-killswitch --sleeve crypto
```

日志：`state/bot.log`，成交记录：`state/trades.csv`。

## 风控一览（`config.yaml` → `risk`）

| 参数 | 默认 | 作用 |
|---|---|---|
| `max_drawdown_kill` | 25% | 仓位组净值较峰值回撤超过此值 → 全部卖出并暂停，需手动 reset |
| `max_order_value` | 5000 | 单笔买单上限（卖单不受限，减仓永远优先） |
| `max_orders_per_run` | 20 | 单次运行订单数上限，防止程序异常时乱下单 |
| `max_price_age_days` | 5 | 行情数据过旧则拒绝交易 |

## 德国/欧盟注意事项（非税务建议，请咨询 Steuerberater）

- **美国 ETF 买不了**：PRIIPs/KID 规定使欧盟散户无法购买 SPY、QQQ 等美国注册 ETF；个股不受影响。若想交易指数，可用 UCITS ETF（如 LSE 上的 CSPX），需在 `brokers/ibkr.py` 中扩展合约定义。
- **股票**：资本利得缴纳 Abgeltungsteuer（25% + Soli + 可能的教会税）。IBKR 是外国券商，不代扣税，需自行在 Anlage KAP 申报；保留 `state/trades.csv` 与 IBKR 年度报表。
- **加密货币**：持有超过 1 年卖出免税；1 年内卖出的收益按个人所得税率纳税（每年 1000 € Freigrenze：超过则全额纳税，不是只对超出部分纳税）。本策略会产生 1 年内的交易，请在决策时考虑这一点。
- **汇率**：股票以 USD 计价，EUR/USD 波动同样影响以欧元计的收益。

## 后续可改进

- 均线加缓冲带（例如跌破均线 2% 才卖出），进一步减少震荡市的来回交易
- Telegram / 邮件告警
- 限价单执行、成交后与持仓对账
- UCITS ETF / 欧股支持
