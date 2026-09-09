"""DDS 查询理解与确定性改写；不调用模型或基础设施 SDK。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class QueryIntent(StrEnum):
    INSTALLATION = "installation"
    CONFIGURATION = "configuration"
    API_USAGE = "api_usage"
    QOS = "qos"
    TROUBLESHOOTING = "troubleshooting"
    PERFORMANCE_TUNING = "performance_tuning"
    GENERAL = "general"


class QueryEntityKind(StrEnum):
    API = "api"
    STRUCT = "struct"
    ENUM = "enum"
    ERROR_CODE = "error_code"
    TERM = "term"
    ABBREVIATION = "abbreviation"


@dataclass(frozen=True, slots=True)
class QueryEntity:
    kind: QueryEntityKind
    surface: str
    canonical: str


@dataclass(frozen=True, slots=True)
class QueryAnalysis:
    original_query: str
    normalized_query: str
    intent: QueryIntent
    entities: tuple[QueryEntity, ...]
    subqueries: tuple[str, ...]
    ambiguous: bool


_ABBREVIATIONS = {
    "DP": ("DomainParticipant", "DDS_DomainParticipant"),
    "DW": ("DataWriter", "DDS_DataWriter"),
    "DR": ("DataReader", "DDS_DataReader"),
}

_TERM_ALIASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"域参与者|domain\s*participant", re.I), "DomainParticipant DDS_DomainParticipant"),
    (re.compile(r"数据写入者|数据写者|写端|data\s*writer", re.I), "DataWriter DDS_DataWriter"),
    (re.compile(r"数据读取者|数据读者|读端|data\s*reader", re.I), "DataReader DDS_DataReader"),
    (re.compile(r"发布者|publisher", re.I), "Publisher DDS_Publisher"),
    (re.compile(r"订阅者|subscriber", re.I), "Subscriber DDS_Subscriber"),
    (re.compile(r"等待集|wait\s*set", re.I), "WaitSet DDS_WaitSet"),
    (re.compile(r"可靠性|reliability", re.I), "ReliabilityQosPolicy DDS_ReliabilityQosPolicy"),
    (re.compile(r"持久性|耐久性|durability", re.I), "DurabilityQosPolicy DDS_DurabilityQosPolicy"),
    (re.compile(r"历史深度|history\s*depth", re.I), "HistoryQosPolicy DDS_HistoryQosPolicy"),
    (re.compile(r"截止时间|deadline", re.I), "DeadlineQosPolicy DDS_DeadlineQosPolicy"),
    (re.compile(r"存活性|活跃性|liveliness", re.I), "LivelinessQosPolicy DDS_LivelinessQosPolicy"),
)

_API_MAPPINGS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (
        re.compile(r"创建(?:域)?参与者|create\s+(?:domain\s*)?participant", re.I),
        ("DDS_DomainParticipantFactory_create_participant",),
    ),
    (
        re.compile(r"创建发布者|create\s+publisher", re.I),
        ("DDS_DomainParticipant_create_publisher",),
    ),
    (
        re.compile(r"创建订阅者|create\s+subscriber", re.I),
        ("DDS_DomainParticipant_create_subscriber",),
    ),
    (
        re.compile(r"创建(?:数据)?写(?:入)?者|创建\s*DW|create\s+(?:data\s*)?writer", re.I),
        ("DDS_Publisher_create_datawriter",),
    ),
    (
        re.compile(r"创建(?:数据)?读(?:取)?者|创建\s*DR|create\s+(?:data\s*)?reader", re.I),
        ("DDS_Subscriber_create_datareader",),
    ),
    (
        re.compile(r"创建主题|create\s+topic", re.I),
        ("DDS_DomainParticipant_create_topic",),
    ),
    (
        re.compile(r"发送数据|写入数据|发布样本|write\s+(?:data|sample)", re.I),
        ("DDS_DataWriter_write",),
    ),
    (
        re.compile(r"读取数据|读取样本|read\s+(?:data|sample)", re.I),
        ("DDS_DataReader_read",),
    ),
    (
        re.compile(r"取走数据|取走样本|take\s+(?:data|sample)", re.I),
        ("DDS_DataReader_take",),
    ),
    (
        re.compile(r"等待条件|等待触发|wait\s+(?:for\s+)?condition", re.I),
        ("DDS_WaitSet_wait",),
    ),
)

_INTENT_TERMS: dict[QueryIntent, tuple[str, ...]] = {
    QueryIntent.INSTALLATION: (
        "安装",
        "部署",
        "依赖",
        "系统要求",
        "环境准备",
        "install",
        "setup",
        "deploy",
        "prerequisite",
    ),
    QueryIntent.CONFIGURATION: (
        "配置",
        "配置文件",
        "环境变量",
        "许可证",
        "license",
        "configure",
        "configuration",
        "setting",
    ),
    QueryIntent.API_USAGE: (
        "接口",
        "函数",
        "方法",
        "参数",
        "返回值",
        "调用",
        "示例",
        "api",
        "function",
        "parameter",
        "return",
        "example",
    ),
    QueryIntent.QOS: (
        "qos",
        "可靠性",
        "持久性",
        "耐久性",
        "历史深度",
        "截止时间",
        "存活性",
        "reliability",
        "durability",
        "history",
        "deadline",
        "liveliness",
    ),
    QueryIntent.TROUBLESHOOTING: (
        "错误",
        "错误码",
        "失败",
        "异常",
        "崩溃",
        "超时",
        "无法",
        "不能",
        "排查",
        "error",
        "failed",
        "failure",
        "exception",
        "crash",
        "timeout",
        "troubleshoot",
    ),
    QueryIntent.PERFORMANCE_TUNING: (
        "性能",
        "调优",
        "延迟",
        "吞吐",
        "抖动",
        "占用率",
        "performance",
        "tuning",
        "latency",
        "throughput",
        "jitter",
        "benchmark",
    ),
    QueryIntent.GENERAL: (),
}

_INTENT_EXPANSIONS = {
    QueryIntent.INSTALLATION: "ZRDDS 安装步骤 系统要求 依赖 环境准备 验证",
    QueryIntent.CONFIGURATION: "ZRDDS 配置项 配置文件 环境变量 许可证 初始化",
    QueryIntent.API_USAGE: "DDS 接口 使用方法 参数 返回值 调用示例",
    QueryIntent.QOS: "DDS QoS policy 配置 兼容性 默认值 生效范围",
    QueryIntent.TROUBLESHOOTING: "ZRDDS 错误码 原因 排查步骤 日志 修复方法",
    QueryIntent.PERFORMANCE_TUNING: "DDS 性能调优 延迟 吞吐 资源占用 参数建议",
    QueryIntent.GENERAL: "DDS 概念 接口 配置 示例",
}

_VAGUE_MARKERS = re.compile(
    r"怎么用|如何用|有问题|有故障|不工作|用不了|不能用|怎么配|怎么设置|"
    r"报错了|失败了|很慢|性能差|how\s+to\s+use|not\s+working|doesn['’]?t\s+work",
    re.I,
)
_DDS_IDENTIFIER = re.compile(r"\b(?:DDS|ZRDDS)_[A-Za-z0-9_]+\b")
_ERROR_IDENTIFIER = re.compile(
    r"\b(?:DDS_RETCODE_[A-Z0-9_]+|[A-Z][A-Z0-9_]*(?:ERROR|ERR|FAILED|TIMEOUT)[A-Z0-9_]*)\b"
)


def analyze_query(query: str) -> QueryAnalysis:
    """Normalize one user query and produce at most three deterministic retrieval queries."""

    original = _normalize_space(unicodedata.normalize("NFKC", query))
    if not original:
        raise ValueError("query must not be empty")

    expansions: list[str] = []
    entities: list[QueryEntity] = []
    for surface, values in _abbreviation_matches(original):
        expansions.extend(values)
        entities.append(QueryEntity(QueryEntityKind.ABBREVIATION, surface, values[0]))
    for pattern, canonical in _TERM_ALIASES:
        match = pattern.search(original)
        if match is None:
            continue
        expansions.append(canonical)
        entities.append(QueryEntity(QueryEntityKind.TERM, match.group(0), canonical.split()[0]))
    mapped_apis: list[str] = []
    for pattern, api_names in _API_MAPPINGS:
        if pattern.search(original) is None:
            continue
        mapped_apis.extend(api_names)
        expansions.extend(api_names)
        entities.extend(QueryEntity(QueryEntityKind.API, original, api) for api in api_names)
    entities.extend(_explicit_entities(original))
    entities = list(_deduplicate_entities(entities))

    normalized = _append_unique(original, expansions)
    intent = _classify_intent(original, entities)
    ambiguous = bool(_VAGUE_MARKERS.search(original)) and not any(
        entity.kind in {QueryEntityKind.API, QueryEntityKind.ERROR_CODE}
        and entity.surface == entity.canonical
        for entity in entities
    )
    subqueries = _build_subqueries(normalized, intent, entities, mapped_apis, ambiguous)
    return QueryAnalysis(original, normalized, intent, tuple(entities), subqueries, ambiguous)


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _append_unique(original: str, additions: list[str]) -> str:
    result = original
    folded = original.casefold()
    for addition in additions:
        for token in addition.split():
            if token.casefold() in folded:
                continue
            result = f"{result} {token}"
            folded = f"{folded} {token.casefold()}"
    return result


def _abbreviation_matches(query: str) -> tuple[tuple[str, tuple[str, str]], ...]:
    matches: list[tuple[str, tuple[str, str]]] = []
    for abbreviation, values in _ABBREVIATIONS.items():
        match = re.search(rf"(?<![A-Za-z0-9_]){abbreviation}(?![A-Za-z0-9_])", query, re.I)
        if match is not None:
            matches.append((match.group(0), values))
    return tuple(matches)


def _explicit_entities(query: str) -> tuple[QueryEntity, ...]:
    entities: list[QueryEntity] = []
    for match in _DDS_IDENTIFIER.finditer(query):
        value = match.group(0)
        if _ERROR_IDENTIFIER.fullmatch(value):
            kind = QueryEntityKind.ERROR_CODE
        elif re.search(r"(?:_KIND|_STATUS|_MASK|_QOS_POLICY_ID)$", value):
            kind = QueryEntityKind.ENUM
        elif re.search(r"(?:Qos|Listener|Status|Seq|TypeSupport)$", value):
            kind = QueryEntityKind.STRUCT
        else:
            kind = QueryEntityKind.API
        entities.append(QueryEntity(kind, value, value))
    for match in _ERROR_IDENTIFIER.finditer(query):
        value = match.group(0)
        entities.append(QueryEntity(QueryEntityKind.ERROR_CODE, value, value))
    return tuple(entities)


def _deduplicate_entities(entities: list[QueryEntity]) -> tuple[QueryEntity, ...]:
    result: list[QueryEntity] = []
    seen: set[tuple[QueryEntityKind, str]] = set()
    for entity in entities:
        key = (entity.kind, entity.canonical.casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append(entity)
    return tuple(result)


def _classify_intent(query: str, entities: list[QueryEntity]) -> QueryIntent:
    folded = query.casefold()
    scores: dict[QueryIntent, int] = {
        intent: sum(1 for term in terms if term.casefold() in folded)
        for intent, terms in _INTENT_TERMS.items()
        if intent is not QueryIntent.GENERAL
    }
    if any(entity.kind is QueryEntityKind.ERROR_CODE for entity in entities):
        scores[QueryIntent.TROUBLESHOOTING] += 3
    if any(
        entity.kind in {QueryEntityKind.API, QueryEntityKind.STRUCT, QueryEntityKind.ENUM}
        for entity in entities
    ):
        scores[QueryIntent.API_USAGE] += 1
    priority = (
        QueryIntent.TROUBLESHOOTING,
        QueryIntent.QOS,
        QueryIntent.PERFORMANCE_TUNING,
        QueryIntent.INSTALLATION,
        QueryIntent.CONFIGURATION,
        QueryIntent.API_USAGE,
    )
    best = max(priority, key=lambda intent: (scores[intent], -priority.index(intent)))
    return best if scores[best] > 0 else QueryIntent.GENERAL


def _build_subqueries(
    normalized: str,
    intent: QueryIntent,
    entities: list[QueryEntity],
    mapped_apis: list[str],
    ambiguous: bool,
) -> tuple[str, ...]:
    if not ambiguous:
        return (normalized,)
    canonical = (
        " ".join(dict.fromkeys(entity.canonical for entity in entities if entity.canonical))
        or "DDS ZRDDS"
    )
    intent_query = f"{canonical} {_INTENT_EXPANSIONS[intent]}"
    api_focus = " ".join(dict.fromkeys(mapped_apis))
    if not api_focus and any("DataWriter" in entity.canonical for entity in entities):
        api_focus = "DDS_Publisher_create_datawriter DDS_DataWriter_write"
    elif not api_focus and any("DataReader" in entity.canonical for entity in entities):
        api_focus = "DDS_Subscriber_create_datareader DDS_DataReader_read DDS_DataReader_take"
    elif not api_focus and any("DomainParticipant" in entity.canonical for entity in entities):
        api_focus = "DDS_DomainParticipantFactory_create_participant"
    if not api_focus:
        api_focus = f"{canonical} DDS 接口 结构体 枚举 错误码"
    return tuple(
        dict.fromkeys((normalized, _normalize_space(intent_query), _normalize_space(api_focus)))
    )[:3]
