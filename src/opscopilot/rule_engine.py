from __future__ import annotations

from .models import AnalysisResult, IncidentEvent


POSSIBLE_CAUSES_MAP = {
    "high_cpu": [
        "请求流量突增",
        "应用内部存在高计算开销",
        "资源限制偏低",
        "下游阻塞导致线程堆积",
    ],
    "high_memory": [
        "内存泄漏",
        "缓存增长异常",
        "资源限制偏低",
        "批处理或大对象占用内存",
    ],
    "pod_crashloopbackoff": [
        "启动失败",
        "配置或密钥错误",
        "依赖不可用",
        "探针配置不合理",
    ],
    "mysql_too_many_connections": [
        "连接泄漏",
        "流量突增耗尽连接池",
        "max_connections 设置偏低",
        "慢查询导致连接长期占用",
    ],
    "nginx_5xx_spike": [
        "上游服务异常",
        "上游超时",
        "配置或发布变更引入问题",
        "网关资源不足",
    ],
}

SUGGESTED_CHECKS_MAP = {
    "high_cpu": [
        "检查近 15 分钟 QPS 与延迟变化",
        "检查最近是否有发布或配置变更",
        "检查应用日志中的异常或热点路径",
        "检查是否存在重启、限流或 OOM 指标",
    ],
    "high_memory": [
        "检查内存趋势与峰值时段",
        "检查 GC 行为或内存分配情况",
        "检查最近发布或开关变更",
        "检查是否出现 OOM Kill 或频繁重启",
    ],
    "pod_crashloopbackoff": [
        "检查 Pod 事件与 describe 输出",
        "检查当前和上一轮容器日志",
        "检查配置、环境变量、密钥是否正确",
        "检查探针与启动命令是否合理",
    ],
    "mysql_too_many_connections": [
        "检查当前连接数及状态分布",
        "检查应用连接池设置与超时",
        "检查慢查询与锁等待",
        "检查近期流量与发布变化",
    ],
    "nginx_5xx_spike": [
        "检查 5xx 类型分布与时间窗口",
        "检查上游服务健康度与延迟",
        "检查 nginx error log 与 timeout 信息",
        "检查近期发布或配置变更",
    ],
}


class RuleBasedAnalyzer:
    """Deterministic baseline analyzer.

    Future LLM adapters can implement the same `generate` signature.
    """

    def __init__(self) -> None:
        self.last_metadata: dict[str, str | bool] = {}

    def generate(self, event: IncidentEvent, context: str) -> AnalysisResult:
        _ = context  # reserved for future context-aware scoring / prompt building

        summary = (
            f"{event.service} 出现 {event.event_type} 事件，"
            f"当前严重级别为 {event.severity}，需要进行初步排查。"
        )

        self.last_metadata = {
            "mode": "rule",
            "llm_configured": False,
            "llm_called": False,
            "llm_used": False,
            "fallback": False,
            "fallback_reason": "rule_mode",
        }

        return AnalysisResult(
            summary=summary,
            possible_causes=POSSIBLE_CAUSES_MAP.get(event.event_type, ["需要进一步分析"]),
            suggested_checks=SUGGESTED_CHECKS_MAP.get(event.event_type, ["需要补充标准排查项"]),
            recommended_refs=[],
            confidence="medium",
        )
