# Hummingbot Workspace Agent Guidelines

本文档为 Hummingbot 项目中 AI Agent 与开发者的**核心行动规范与流程指引**。为保持大模型上下文的高效、低延迟与规则聚焦，本文档仅维护核心准则与规则索引；所有详细排错案例、系统机制与实战数据均沉淀于 `docs/` 目录中。

---

## 1. 交互与经验持续沉淀规则 (Continuous Documentation Rule)
- **事前查阅**：Agent 在执行开发、排错或回答用户问题前，必须优先查阅 `docs/` 下的已有文档（特别是 [`docs/FUNDING_ARBITRAGE_KNOWLEDGE_BASE.md`](file:///home/neo/projects/hummingbot/docs/FUNDING_ARBITRAGE_KNOWLEDGE_BASE.md)），充分继承历史经验，严禁重复踩坑。
- **事后留档**：在与用户的交互过程中，凡是**解决了核心 Bug、排查了新报错、识别出业务陷阱、优化了核心算法或沉淀了重要财务数据**，必须主动将其整理为结构化文档存入项目 `docs/` 目录（可追加更新到既有知识库，或新建对应专题文档），**严禁将宝贵经验遗忘在会话上下文中**。
- **索引维护**：新文档创建后，必须在 [`docs/FUNDING_ARBITRAGE_KNOWLEDGE_BASE.md`](file:///home/neo/projects/hummingbot/docs/FUNDING_ARBITRAGE_KNOWLEDGE_BASE.md) 或相关索引中登记，方便后续项目和 Agent 直接检索。

---

## 2. Git Workflow & 自动语义化提交
- **任务完成自动整理提交**：在本项目（`hummingbot-dashboard`、`hummingbot-api` 等）中，每当完成代码编写、功能迭代、配置调整、文档沉淀或 Bug 修复后，必须主动执行 Git 整理。
- **Conventional Commits 规范**：使用标准语义化前缀（`feat:`, `fix:`, `refactor:`, `chore:`, `docs:` 等），创建清晰提交并主动 `git push origin <branch>` 推送至远程 GitHub 仓库。
- **工作区整洁性**：严禁将临时日志文件（`*.log`）、临时数据库（`*.sqlite`）或容器临时生成物误提入版本库，随时保持 clean 状态。

---

## 3. 核心工程与环境红线
- **容器与文件权限规范**：容器默认以 root 运行，所有映射到宿主机挂载卷的文件（`bots/conf`, `bots/instances`, `bots/data`）必须保持属主为宿主机用户（UID 1000:1000），严禁遗留 root 属主导致 `Permission denied`。
- **配置双向同步**：修改策略参数时，必须同步兼顾模板配置目录（`bots/conf/scripts/`）与活跃运行实例目录（`bots/instances/<id>/conf/scripts/`），防止配置漂移。

---

## 4. 资金费率量化策略与风控准则概要
- **回本周期硬性拦截（Payback Period Barrier）**：严禁倒贴点差，开仓摩擦成本（点差+双边手续费）回本时间 `max_payback_hours` 必须 ≤ 12 小时，超标坚决不开仓。
- **动态容量定额（Dynamic Sizing）**：采集双边 Top 25 档挂单深度，开仓额限制在安全容量内（默认 ≤ 30% 盘口深度）。
- **三层实时风控**：费率倒挂止损（≤ -0.1%/天）、强平安全垫防御（单腿安全垫 ≤ 8% 主动双边平仓）、综合止盈锁定。
- **详细技术案例与数学公式**：完整公式、12 大实战排错案例与两派套利流派解析，详见 [`docs/FUNDING_ARBITRAGE_KNOWLEDGE_BASE.md`](file:///home/neo/projects/hummingbot/docs/FUNDING_ARBITRAGE_KNOWLEDGE_BASE.md)。

---

## 5. 财务口径与数据对账准则
- **单边货值 vs 双边货值**：对冲套利为双边开仓，严格区分 `单边货值` 与 `双边总货值 (2 × 单边货值)`。
- **真实本金收益率 (ROE)**：ROE 必须以双边真实实缴保证金总额作为分母（`ROE = 24h 预计利息现金流 × 365 / 真实双边总保证金`）。
- **标的生命周期归档**：淘汰标的必须通过优雅平仓归档至 `closed_trades` 历史表，标注 `close_reason`，严禁无声抹除数据。

---

## 6. 官方主版本同步与无缝升级准则 (Upstream Upgrade Discipline)
- **代码物理隔离与插件插槽**：所有针对官方仓库（`hummingbot-dashboard`、`hummingbot-api` 等）的新功能，必须在独立自包含组件（如 `frontend/components/`）中编写，官方核心文件仅保留 2~3 行轻量插槽调用，严禁在官方主流程中硬编码海量业务代码，确保与官方主干合并冲突率为 0%。
- **双源拓扑与一键运维**：必须配置 `upstream` 官方主源，并使用根目录自动化脚本 [`sync-upstream.sh`](file:///home/neo/projects/hummingbot/sync-upstream.sh) 维护状态。
- **详细升级 SOP 与避坑流程**：详见 [`docs/UPSTREAM_UPGRADE_SOP.md`](file:///home/neo/projects/hummingbot/docs/UPSTREAM_UPGRADE_SOP.md)。

