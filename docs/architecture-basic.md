# OpsCopilot MVP 架构草图

## 当前最小链路

1. 读取一条结构化 incident event
2. 按 event_type 匹配对应知识卡片
3. 生成结构化分析结果
4. 输出 summary / possible_causes / suggested_checks / recommended_refs / confidence

## 当前特点
- 不依赖公司内部数据
- 使用公开样本 / 自造样本驱动
- 先用规则化映射打底，后续再接入 LLM 与 RAG

## 后续演进方向
- 接入 LLM 生成更自然的分析摘要
- 将知识卡片纳入 RAG 检索链路
- 加入评测样本与回归对比
- 增加日志、trace、token/cost 观测
