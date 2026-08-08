"""节点类型配置表（单一事实来源）。

图分析模块「部分解耦」：算法引擎（社区发现 / 中心性 / 单向投影）与业务域无关，可零改动复用；
本文件把「有哪些节点类型、各自对应什么存储标签 / 摘要字段 / 投影说明」从散落硬编码收口成
一张表。接入新业务（新图谱）只需改这一张表，无需触碰算法或路由代码。

设计原则（对齐 UI 约定）：``display_label`` / ``projection_blurb`` 是给用户看的界面语言，
不暴露后台标识（如 neo4j_label、is_goty 内部机制）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeTypeSpec:
    """单个节点类型的全部元信息。"""

    group: str  # 内部 group 名（也作 scope id）
    neo4j_label: str  # Neo4j 节点标签
    is_goty: bool | None = None  # 是否需要用 is_goty 区分（game=False / goty=True / 其他 None）
    summary_fields: tuple = ()  # 前端卡片摘要取用的 raw 字段
    display_label: str = ""  # 下拉显示名（界面对用户，非后台标识）
    projection_blurb: str = ""  # 同类投影的物理含义说明（界面对用户）
    supports_projection: bool = True  # 是否提供「同类投影」分析范围


# 当前业务（游戏知识图谱）的节点类型词汇表。换业务只改这一张表。
GRAPH_SCHEMA: dict[str, NodeTypeSpec] = {
    "game": NodeTypeSpec(
        group="game",
        neo4j_label="Game",
        is_goty=False,
        summary_fields=("title_zh", "year", "genre", "developer", "player_rating"),
        display_label="仅游戏（同类投影）",
        projection_blurb="把游戏按「共享类型/工作室/奖项」的数量聚类，得到「相似游戏」社群——这才是同类节点的亲和社群，物理含义最清晰，推荐先用它理解社区发现。",
    ),
    "goty": NodeTypeSpec(
        group="goty",
        neo4j_label="Game",
        is_goty=True,
        summary_fields=("title_zh", "year", "genre", "developer", "player_rating"),
        display_label="仅年度游戏（同类投影）",
        projection_blurb="在历年 GOTY 获奖游戏之间，按共享类型/工作室聚类，看神作们是否分门别类成簇。",
    ),
    "genre": NodeTypeSpec(
        group="genre",
        neo4j_label="Genre",
        summary_fields=("name", "parent", "tier"),
        display_label="仅类型（同类投影）",
        projection_blurb="把类型按「被哪些游戏共同拥有」聚类，得到「经常结伴出现的类型」社群（如开放世界 + 角色扮演）。",
    ),
    "studio": NodeTypeSpec(
        group="studio",
        neo4j_label="Studio",
        summary_fields=("name_zh", "country", "founded"),
        display_label="仅工作室（同类投影）",
        projection_blurb="按「共同开发的游戏」聚类工作室；若多为独立簇，说明本数据里工作室各自出品、少有重叠。",
    ),
    "award": NodeTypeSpec(
        group="award",
        neo4j_label="Award",
        summary_fields=("name", "year", "body"),
        display_label="仅奖项（同类投影）",
        projection_blurb="按「授予同一批游戏」聚类奖项，看奖项偏好是否成派系。",
    ),
}


ALL_SCOPE: dict[str, str] = {
    "id": "all",
    "label": "全图（混合类型）",
    "blurb": "所有类型节点一视同仁。社团往往是「一个游戏 + 它的类型/工作室/奖项」这样的属性簇——它反映的是实体与属性的绑定，物理含义不同于同类节点的亲和社群。",
}


def list_scopes() -> list[dict[str, str]]:
    """分析范围目录：``all`` + 各节点类型的同类投影。前端据此渲染下拉与物理含义说明。"""
    scopes = [dict(ALL_SCOPE)]
    for spec in GRAPH_SCHEMA.values():
        if spec.supports_projection:
            scopes.append(
                {"id": spec.group, "label": spec.display_label, "blurb": spec.projection_blurb}
            )
    return scopes


def scope_ids() -> set[str]:
    """合法 scope 集合：``all`` + 所有支持投影的节点类型。供路由校验。"""
    return {"all", *[s.group for s in GRAPH_SCHEMA.values() if s.supports_projection]}


def group_predicate(group: str | None) -> str:
    """把 group 名翻译成 Cypher 的标签/属性谓词（不含 WHERE 关键字）。"""
    spec = GRAPH_SCHEMA.get(group or "")
    if spec is None:
        return ""
    clause = f"n:{spec.neo4j_label}"
    if spec.is_goty is True:
        clause += " AND n.is_goty = true"
    elif spec.is_goty is False:
        clause += " AND (n.is_goty IS NULL OR n.is_goty = false)"
    return clause


def group_of_node(labels: set, props: dict) -> str:
    """从 Neo4j 节点的 labels + 属性反查 group（处理 game/goty 共用 Game 标签）。"""
    for spec in GRAPH_SCHEMA.values():
        if spec.neo4j_label in labels:
            if spec.is_goty is None:
                return spec.group
            if bool(props.get("is_goty")) == (spec.is_goty is True):
                return spec.group
    return "node"
