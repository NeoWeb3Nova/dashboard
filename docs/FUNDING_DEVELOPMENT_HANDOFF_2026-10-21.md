# 资金费套利开发交接：提交、验证与工作区边界

2026-10-21。用户已明确授权把本轮已审计改动提交、推送至GitHub默认分支，并整理临时worktree，以便另一个模型开发其他模块。此前文档中的“远程推送待授权”是历史状态，现已被本次授权取代。推送结果以本次Git交接验证清单为准。

## 仓库与下一轮开发起点

- API：`/home/neo/projects/hummingbot/hummingbot-api`，origin=`NeoWeb3Nova/hummingbot-api`，默认分支`main`。
- Core：`/home/neo/projects/hummingbot/hummingbot-core`，origin=`NeoWeb3Nova/hummingbot`，默认分支`master`。
- Dashboard：`/home/neo/projects/hummingbot/hummingbot-dashboard`，origin=`NeoWeb3Nova/dashboard`，默认分支`main`。

本轮资金费代码均已合入本地默认分支；推送使用常规快进，不强制推送、不改写历史。下一轮应先fetch origin，再从对应origin/main或origin/master创建新的独立worktree和功能分支。默认工作目录保留运行挂载，不在正在运行的目录切换分支、reset、clean或覆盖文件。仍遵守根目录AGENTS.md及知识库。

## 本轮验证与尚未完成的目标

资金费前端48项回归通过，当前frontend/test代码与受测版本5058b98一致；实际实例页和10行费率表已核对。资金费API/Core五个脚本与69项原生回归所验版本哈希一致。后续提交仅增加运行、恢复、HL基准费率及交接文档，没有改动这些受测策略代码。

交付包括：公开行情Dry-run隔离、逐币周期与历史资金费记账、双腿数量/四笔费用/预算/盘口约束、动态选币及接入回滚、独立费率快照确认、前后端快照和控制一致性，以及缺失数据、费率方向、精度和排序修正。官方连接器、时钟和执行器架构保持既定边界。

**工程回归不代表盈利通过。** 当前仍缺少足够的两腿开仓→跨结算→退出→资金费/手续费/基差完整闭环样本。不得因零亏损、短期正收益或没有成交而宣称策略有正期望。

## 正在运行的模拟与限制

资金费容器`funding-arb-dynamic-paper-neo-20261021-055610`在北京时间17:58:07恢复；新独立24h采样从2026-10-21 18:06:01起，预计2026-10-22 18:06:01结束。以实际acceptance.json为准。旧101/91、72/66两轮中断均完整保留。

实际输出在API宿主`bots/data/funding-dynamic-acceptance-20261021T100601Z/`。采样器在既有API容器内运行，不跨API容器重启存活；改其他模块时不要误重启该容器而无声中断验收。机器人保持Dry-run、10,000预算、1x，日毛费差0.12%、完整往返成本回本≤12h；没有接真实订单。

网络曾出现实际418与TLS超时，已保留原始观察；REST成功不等于实时盘口正常。恢复后仍应看两所盘口/规则、费率时间、扫描新鲜度和真实样本健康。HL大量币日化0.03%已通过原始逐币接口核对，源于小时基准0.00125%，不是写死常量。

## 必须保留的并行基差修改

主工作区有三处在本轮资金费审计开始前就存在、且本轮未修改的基差文件：

- API `bots/scripts/v2_basis_stat_arb.py`。
- Core `scripts/v2_basis_stat_arb.py`。
- Dashboard `frontend/components/basis_stat_arb_view.py`。

这些文件当前仍为未提交状态，**未混入本轮资金费推送，不属于本次完整审计的交付**。原地保留给负责基差的模型，没有stash、reset或强行提交。额外备份于Windows项目`.work/funding-git-handoff-20261021/parallel-basis/`，WSL路径`/mnt/d/Web3.0/套利大脑/.work/funding-git-handoff-20261021/parallel-basis/`，包含各文件副本、基于对应HEAD的binary patch、基准提交及SHA256清单。

另一个模型开发基差时，先确认这三处改动与自己的工作归属；需要带到新worktree时按manifest的基准审查并应用patch，不重复叠加。不把API/Core两个副本当成两套独立实现，后续同步核对。

## 临时worktree整理原则

只移除本轮创建、已合并、无未提交或未跟踪业务文件、无运行进程或Docker挂载依赖的临时worktree。只允许忽略项为Python字节码缓存；发现其他产物则保留。使用git worktree remove，不force，不删分支历史、数据库、日志、配置、凭据或运行实例。Windows备用worktree也先核对远程已包含其HEAD，再用Windows Git清理；no-checkout缓存仓库作为取证缓存保留。

Git整理不需要重启任何容器或前端。整理前后都要复核并行基差文件哈希、容器启动时间、验收指针及采样进程。最终远程分支SHA与清理清单保存在本次交接记录中。
