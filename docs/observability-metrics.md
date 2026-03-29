# OpsCopilot Observability & Metrics Design（Week 7 Day 1）

## 目标

在不把系统复杂度一下子拉爆的前提下，为 OpsCopilot 建立第一轮**最小 observability 面**，让系统开始能够回答这些问题：

- 这次请求的唯一标识是什么？
- 哪一步最慢？
- 哪一步最容易出错？
- 一次分析大概消耗了多少 token / cost？
- 当前结果是主路径成功，还是降级成功？

Week 7 的重点不是一上来接完整 tracing 平台，也不是上 dashboard，而是：

> 先把最关键、最能支撑工程判断的日志与指标打出来。

---

## 为什么这周先做最小 observability

Week 6 已经把 workflow 做成了：
- 有固定步骤
- 有 path decision
- 有 degraded / fallback / continue 语义
- 有最小场景回归

下一步最自然的不是继续加功能，而是让这些行为变得：
- 更可观测
- 更可量化
- 更容易复盘

否则 workflow 虽然能跑，但遇到性能、成本、失败模式问题时，仍然会重新变成“只能猜”的系统。

---

## 本周先关注的 4 类最小观测项

### 1. request id

#### 目标
每次 pipeline run 都有一个唯一 request id，方便：
- 串联本次运行的所有 metadata
- 关联日志与输出
- 后续做 tracing / case replay / incident review

#### 最小要求
- 每次 run 自动生成 request id
- 写入 `last_run_metadata`
- debug 输出时能看到
- 不要求分布式唯一，只要本地唯一且稳定可读即可

建议字段：
- `request_id`

建议形式：
- UUID / 短 UUID / 时间戳+随机串

---

### 2. step latency

#### 目标
知道：
- 总耗时是多少
- retrieve / checks / final_analysis 各自花了多久
- 哪一步最慢

#### 最小要求
- 记录 pipeline 总耗时
- 记录每个 workflow step 的耗时毫秒数
- 写入 metadata / trace

建议字段：
- `duration_ms`
- `step_latency_ms`
- `total_duration_ms`

当前更推荐：
- 每个 step trace 里记录 `duration_ms`
- workflow overview/pipeline metadata 里聚合 `total_duration_ms`

---

### 3. token / cost

#### 目标
知道：
- 一次请求是否调用了 LLM
- 输入/输出 token 大概多少
- 成本大概多少
- fallback 到 rule 时是否避免了 LLM 成本

#### 最小要求
第一轮不追求极精确，只要做到：
- 如果 LLM provider 返回 usage，就接住
- 若拿不到 usage，也要明确标记 unavailable，而不是假装没有
- pipeline metadata 里有统一 `token_usage` / `cost_estimate`

建议字段：
- `token_usage.prompt_tokens`
- `token_usage.completion_tokens`
- `token_usage.total_tokens`
- `cost_estimate.input_cost`
- `cost_estimate.output_cost`
- `cost_estimate.total_cost`
- `token_usage_available`

这周不要求：
- 完整多 provider 精细计费表
- 严格财务级精度

---

### 4. error type / degraded reason

#### 目标
知道：
- 哪一类错误最常见
- 是 retriever 更容易坏，还是 LLM synthesis 更容易坏
- degraded success 的主要原因是什么

#### 最小要求
- 统一保留当前 Week 5/6 已有错误语义
- 让 pipeline 级 metadata 能汇总本次 run 的主要 error type / degraded reason
- 让后续做计数统计时更容易聚合

建议字段：
- `error_type`
- `degraded_reason`
- `had_error`
- `had_fallback`
- `run_status`

---

## 建议的最小日志/指标面

### Workflow step 级
每个 step trace 至少包含：
- `step`
- `status`
- `path_decision`
- `degraded`
- `duration_ms`
- `details`
- 可选：`error_type`

### Pipeline 级
`last_run_metadata` 建议新增/强化：
- `request_id`
- `total_duration_ms`
- `workflow_overview`
- `token_usage`
- `cost_estimate`
- `error_summary`

### Debug 输出级
当 `OPSCOPILOT_DEBUG=1` 时，至少能看到：
- request id
- total duration
- step durations
- token/cost（若可得）
- final path / degraded reason

---

## 本周先不做什么

- 不接 OpenTelemetry
- 不做完整 tracing backend
- 不做 Prometheus exporter
- 不做 Grafana dashboard
- 不做全量日志平台接入
- 不做复杂 sampling / aggregation framework

这些都可以后续做，但不该在 Week 7 一上来一起上。

---

## 建议推进顺序

### Day 1
- 设计最小 observability 字段
- 明确 request id / latency / token-cost / error summary 的边界
- 文档落盘

### Day 2
- 给 pipeline run 补 request id 与总耗时
- 给 workflow step 补 duration_ms

### Day 3
- 打通 token usage / cost estimate 的最小接入
- 没有 usage 时显式标记 unavailable

### Day 4
- 汇总 error_summary / degraded_reason / run-level summary
- 增强 debug 输出

### Day 5
- 统一 metadata 命名与字段兼容性
- 小幅补 README

### Day 6
- 补最小 observability 场景测试
- 重点覆盖：有 LLM usage / 无 usage / fallback / degraded

### Day 7
- 文档收口 + 周总结

---

## 当前结论

Week 7 不是“搞监控平台”，而是做第一轮工程化可观测性闭环：

- request id
- step latency
- token / cost
- error / degraded summary

这会让 OpsCopilot 从“能跑 workflow”进一步变成：

> 能回答系统运行得怎么样、贵不贵、慢不慢、哪里最容易坏。
