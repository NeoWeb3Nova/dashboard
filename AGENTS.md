# Hummingbot Workspace Agent Guidelines

## 1. Git Workflow & Auto-Commit Rule
- **任务完成自动整理提交**：在本项目（包括 `hummingbot-dashboard`、`hummingbot-api`、`hummingbot-core`）中，每当完成代码编写、功能迭代、配置调整或 Bug 修复任务后，必须主动执行 Git 整理。
- **规范提交（Conventional Commits）**：
  - 检查受影响仓库的 `git status` 与 `git diff`。
  - 使用标准的语义化前缀编写清晰明了的 Commit 信息（例如 `feat:`, `fix:`, `refactor:`, `chore:` 等）。
  - 执行 `git add` 并创建本地提交，随后推送至远程 GitHub 仓库（`git push`）。
- **工作区整洁性**：
  - 避免将临时日志文件（如 `*.log`、`*.sqlite`）或容器临时生成物误提入版本库。
  - 保持工作区随时处于 clean 状态。

---

## 2. Docker 容器与文件权限规范 (File Permissions & Ownership)
- **拒绝 Root 权限污染**：
  - Hummingbot 官方容器默认以 root 用户在 `/home/hummingbot` 运行并创建文件。
  - 所有映射到宿主机挂载卷的目录（如 `bots/conf`, `bots/instances`, `bots/data`）必须保持属主为宿主机开发用户（UID 1000:1000）。
  - 任何新建脚本或写入操作严禁留下宿主用户不可写的只读权限，避免触发 `[Errno 13] Permission denied`。
- **配置与实例同步一致性**：
  - 修改策略参数时，必须同时兼顾模板配置目录（`bots/conf/scripts/`）与活跃运行实例目录（`bots/instances/<id>/conf/scripts/`），严禁单边修改引发配置漂移。

---

## 3. 资金费率量化套利核心铁律 (Funding Arbitrage Principles)
- **开仓回本周期硬性拦截（Payback Period Barrier）**：
  - 严禁“为了吃芝麻利息倒贴大点差”。
  - 开仓前必须严格计算入场总摩擦成本（盘口买卖点差 + 双边开仓手续费）与每小时预期利息差。
  - **最大允许回本时间（Max Payback Hours）默认为 12 小时**。如果入场磨损需要持仓超过 12 小时才能抹平，策略必须坚决放弃开仓。
- **基于订单簿深度的动态容量控制（Dynamic Position Sizing）**：
  - 严禁使用固定单一货值（如无脑设定 $10,000）扫单。
  - 必须采集双边交易所 Top 25 档订单簿深度，设定安全承载比例（默认 <= 30% 盘口深度），规避冲击成本与盘口穿透。
- **跨所异步结算认知（Asymmetric Settlement Cycles）**：
  - Hyperliquid（1h 结算）与 Binance（4h/8h 结算）存在天然账期错位。
  - 严禁在持有未满 8 小时完整周期前单方面因某一腿短期扣息产生恐慌误判；收益核算必须以完整结算对齐周期为准。
- **强制三层实时风控（Triple-Layer Risk Sentinel）**：
  - **费率倒挂止损（Funding Diff Stop Loss）**：息差恶化跌破阈值（如 -0.1%/天）立即平仓；
  - **强平防御安全垫（Liquidation Defense）**：单腿市价距离强平线 <= 8% 立即提前平仓防爆；
  - **综合利润止盈（Take Profit）**：总净收益达标（如 1%）锁定利润。

---

## 4. 财务数据口径与前端对账规范 (Accounting & UI Standards)
- **杠杆与资金财务口径严谨性**：
  - 对冲套利是双边开仓（一边多、一边空）。
  - 必须严格区分并分列：
    - `单边名义货值 (Single-leg Notional)`
    - `双边合约总货值 (Gross Notional = 2 * Single-leg Notional)`
    - `真实投入保证金 (Total Margin = Gross Notional / Leverage)`
    - `当前杠杆倍数 (Leverage, 默认 3x)`
- **收益率呈现规范**：
  - 顶栏预期现金流年化回报率必须以**真实双边本金保证金**作为分母（`ROE = Annualized Cashflow / Total Margin`），杜绝以单边或全货值混淆计算。
  - 盘口净盈亏必须透明分列为：【两腿价差损益 (Trading PnL)】 + 【累计已收净资金费 (Funding PnL)】 - 【双边交易手续费】。

---

## 5. 标的全生命周期与历史账本归档规范 (Token Lifecycle & Archiving)
- **严禁无声抹除持仓记录**：
  - 淘汰低产出或僵尸币种时，不能简单将数据从 JSON 中删除。
  - 必须完整记入历史平仓对账表（`closed_trades`），保留明确平仓原因（如 `Manual Rotation: Low Yield Ditch`、`Stop Loss`、`Take Profit`）以及最终开平仓时间戳与总损益。
- **系统知识库联动**：
  - 遇到复杂技术陷阱或新商业模型设计时，必须同步在 [`docs/FUNDING_ARBITRAGE_KNOWLEDGE_BASE.md`](file:///home/neo/projects/hummingbot/docs/FUNDING_ARBITRAGE_KNOWLEDGE_BASE.md) 中更新并持续沉淀。
