# OpsCopilot Tool Calling Design（Week 6 Day 1）

## 目标

在不破坏 Week 5 已有工程化底盘的前提下，为 OpsCopilot 增加一条最小可运行的 **“检索 + 工具 + 总结”** workflow。

当前目标不是做复杂 Agent，也不是做开放式多工具编排，而是把 Tool Calling 先收敛成一条可解释、可回退、可回归的最小链路。

---

## 设计原则

1. **先做最小闭环，不做通用框架**
2. **工具数量控制在 2~3 个**
3. **每个工具都必须有明确输入 / 输出 / 失败语义**
4. **工具失败时要能解释、能回退，不把整条链路拖崩**
5. **复用 Week 5 已有 error semantics / fallback / scenario regression 思路**
6. **先保留当前 retriever + generator 主体结构，不做大规模推翻**

---

## 为什么 Week 6 要先收敛工具边界

如果直接进入“工具越多越强”的路线，很容易出现这些问题：

- 工具定义不稳定，后面难以评测
- 每个工具都像临时脚本，无法形成可维护接口
- 工具失败后没有统一语义，系统行为变黑盒
- workflow 过早复杂化，掩盖真正值得学习的工程问题

所以 Week 6 的重点不是工具多，而是：

> 先把 Tool Calling 做成一个最小、稳定、可观察的工程能力。

---

## 当前建议的 3 个最小工具接口

### Tool 1：`retrieve_knowledge_cards`

#### 作用
根据 incident 从知识卡片中检索最相关的上下文。

#### 为什么它仍然保留为工具
虽然当前系统里已经有 retriever，但在 Week 6 里将其显式提升为“工具”有两个意义：

1. 明确 workflow 中“先取知识”的一步
2. 为后续比较“固定 pipeline”与“工具式编排”打基础

#### 输入
- `event_type`
- `title`
- `description`
- `service`
- `environment`
- `symptoms`
- 可选：`top_k`

#### 输出
- `context_text`
- `reference_paths`
- `matched_cards`
- `returned_count`
- `retriever_mode`
- `metadata`

#### 失败语义
- `external_dependency_error`：例如 chroma 不可用
- `retrieval_empty`：未检索到内容
- 可降级到 local retriever

---

### Tool 2：`extract_structured_checks`

#### 作用
根据 incident + retrieved context，提取一组更结构化的排查检查项。

#### 为什么需要这个工具
当前主输出里已经有 `suggested_checks`，但它们主要来自 rule / llm 直接生成。
Week 6 可以先把“检查建议提取”显式拆成一个工具步骤，让 workflow 更像：

- 先拿知识
- 再抽取检查动作
- 最后做统一总结

这会让多步链路更清楚，也更适合后续观察工具调用价值。

#### 输入
- `incident`
- `retrieved_context`
- 可选：`rule_result`

#### 输出
- `checks`
- `check_categories`
- `tool_notes`
- `metadata`

#### 失败语义
- 工具失败时不应直接中断整个流程
- 可回退为：使用 rule baseline 里的 `suggested_checks`

---

### Tool 3：`build_final_analysis`

#### 作用
将 incident、retrieved context、tool outputs、rule result 统一汇总为最终结构化分析结果。

#### 为什么把它也视作工具
严格说它更接近 workflow 中的 final synthesizer，但在 Week 6 先把“总结”单独显式化，有助于明确：

- 哪些内容来自检索
- 哪些内容来自工具处理
- 哪些内容来自最终综合判断

#### 输入
- `incident`
- `retrieved_context`
- `structured_checks`
- 可选：`rule_result`

#### 输出
保持现有主输出 schema：
- `summary`
- `possible_causes`
- `suggested_checks`
- `recommended_refs`
- `confidence`

#### 失败语义
- 若 LLM 总结失败，可 fallback 到 rule-based 分析结果
- 保持现有 `llm_call_failed` / `output_parse_failed` / fallback 语义

---

## 最小 workflow 设计

### 目标链路

```text
incident
  -> retrieve_knowledge_cards
  -> extract_structured_checks
  -> build_final_analysis
  -> analysis result
```

### 对应语义

1. **检索**：先拿最相关知识
2. **工具处理**：把知识转成更适合行动的检查项
3. **总结**：综合 incident + context + tool outputs 形成最终分析

---

## 与当前系统的关系

Week 6 不是推翻 Week 5，而是在当前结构上做最小演进。

### 当前已有
- `KnowledgeRetriever`
- `AnalysisGenerator`
- `IncidentAnalysisPipeline`
- Rule / LLM 双路径
- fallback / retry / error semantics / scenario regression

### Week 6 最合理的演进方式
不是直接重写成一个“超通用 agent 框架”，而是：

1. 新增 `ToolExecutor` / `WorkflowRunner` 之类的轻量抽象
2. 保留现有 retriever / generator 作为底层能力
3. 先把 workflow metadata 打通
4. 让工具调用成为可观察的一层，而不是黑盒

---

## 建议的最小接口形态

可以先从轻量 Python 接口开始，而不是上来就做 MCP / 外部协议。

示意：

```python
class WorkflowTool(Protocol):
    def run(self, payload: dict) -> dict:
        ...
```

或者更稳一点：

```python
class WorkflowStep(Protocol):
    def execute(self, state: dict) -> dict:
        ...
```

当前更建议 **WorkflowStep** 这种形式，因为本周重点是“最小 workflow 编排”，不是开放式工具生态。

---

## 本周先不做什么

- 不做开放式任意工具注册系统
- 不做多 Agent 协作
- 不做 MCP 接入
- 不做动态 planner / replanner
- 不做复杂权限系统
- 不做很重的状态机框架

---

## Week 6 Day 1 之后的自然推进顺序

### Day 1
- 明确工具边界
- 明确最小 workflow 链路
- 设计文档落盘

### Day 2
- 定义 workflow state / step interface
- 给现有 retriever / generator 找到最小接入点

### Day 3
- 实现 `retrieve_knowledge_cards` + `extract_structured_checks` 最小版本

### Day 4
- 实现 `build_final_analysis` 接入与最小 workflow 跑通

### Day 5
- 增加 workflow metadata / step trace / path decision

### Day 6
- 补最小场景回归：工具成功 / 工具失败 fallback / 空检索 continue

### Day 7
- 文档收口，形成 Week 6 第一轮总结

---

## 当前结论

Week 6 的关键，不是让 OpsCopilot 一下子“像 agent 一样很聪明”，而是先把这件事做成：

- 有明确工具边界
- 有最小可运行 workflow
- 有清晰失败语义
- 能继续复用 Week 5 的工程化资产

这才是当前最稳、也最符合主线目标的推进方式。
