"""社区发现算法的策略模式实现。

设计目标（对应「好的设计原则 + 合适设计模式」）：
- **策略模式（Strategy）**：每种社区发现算法都是一个 ``CommunityDetector`` 具体策略，
  共享统一接口 ``detect`` / ``detect_stepwise``。新增算法 = 新增一个策略类 + 在
  ``DETECTORS`` 注册表登记，路由层与前端无需改动（前端通过 ``/communities/meta``
  动态获取算法目录与参数表单）。
- **单一职责**：``CommFrame`` / ``CommResult`` 只承载数据；``run_detection`` 负责编排；
  各策略只关心「给定图 G 与参数，如何算出社团 / 过程帧」。
- **可选依赖不污染主链路**：Louvain / Infomap 等需第三方包的策略通过
  ``optional_dependency`` 声明，运行时惰性探测；缺失时 ``available()`` 返回 False，
  路由返回清晰的 400（而非 500 / 静默兜底）。
- **教育性动画**：``detect_stepwise`` 产出 ``CommFrame``（步骤解说 + 当前着色 +
  可选质量指标 Q + 被切断的边），前端据此在图谱上逐步重着色，帮助直观理解算法原理。
"""

from __future__ import annotations

import importlib.util
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
from networkx.algorithms.community import modularity

log = logging.getLogger(__name__)


@dataclass
class CommFrame:
    """算法过程的一帧快照，用于教育性动画。

    - ``step``：帧序号（从 0 开始）
    - ``description``：这一步在做什么（中文解说，面向大学生）
    - ``assignment``：当前社团划分 {node_id: community_id}
    - ``metric``：可选质量指标（如模块度 Q），便于观察算法收敛
    - ``highlight_edges``：本步被「切断」的边 [(u, v)]（分裂式算法用）
    - ``highlight_nodes``：本步聚焦点 [(u, v)]（如合并的两团代表节点）
    """

    step: int
    description: str
    assignment: dict[str, int]
    metric: float | None = None
    highlight_edges: list[tuple[str, str]] = field(default_factory=list)
    highlight_nodes: list[str] = field(default_factory=list)


@dataclass
class CommResult:
    algorithm: str
    display_name: str
    params: dict[str, Any]
    communities: list[dict[str, Any]]  # [{id, size, members}]
    assignment: dict[str, int]  # node_id -> community_id（最终划分）
    modularity: float | None = None
    supports_animation: bool = False
    frames: list[CommFrame] = field(default_factory=list)


def _assignment_from_partitions(partitions: list[set]) -> dict[str, int]:
    """把「一组社团（set 列表）」转成 {node_id: community_index} 赋值。"""
    assign: dict[str, int] = {}
    for idx, comm in enumerate(partitions):
        for nid in comm:
            assign[nid] = idx
    return assign


def _pad_isolated(G: nx.Graph, assign: dict[str, int]) -> dict[str, int]:
    """孤立点（未落入任何社团）各自成团，保证覆盖全图节点。"""
    nxt = len(set(assign.values()))
    for nid in G.nodes():
        if nid not in assign:
            assign[nid] = nxt
            nxt += 1
    return assign


def _communities_summary(assign: dict[str, int]) -> list[dict[str, Any]]:
    sizes: dict[int, int] = {}
    for c in assign.values():
        sizes[c] = sizes.get(c, 0) + 1
    return [
        {
            "id": c,
            "size": sizes[c],
            "members": [nid for nid, cc in assign.items() if cc == c],
        }
        for c in sorted(sizes, key=lambda c: -sizes[c])
    ]


class CommunityDetector(ABC):
    """社区发现策略的抽象基类（Strategy 接口）。

    子类只需实现 ``detect``；若算法天然可逐步观察（如贪心合并、标签传播、分裂），
    应同时实现 ``detect_stepwise`` 以产出教育性动画帧。
    """

    # —— 类级元数据（前端目录 / 表单渲染用）——
    name: str = "base"
    display_name: str = "基础算法"
    description: str = ""
    blurb: str = ""  # 一句话教育意义
    supports_animation: bool = False
    optional_dependency: str | None = None  # 可选第三方包名；None 表示零依赖
    params_schema: list[dict[str, Any]] = []

    @classmethod
    def available(cls) -> bool:
        """该策略在当前环境是否可用（惰性探测可选依赖）。"""
        if cls.optional_dependency is None:
            return True
        return importlib.util.find_spec(cls.optional_dependency) is not None

    @abstractmethod
    def detect(self, G: nx.Graph, params: dict[str, Any]) -> CommResult:
        """在图 G 上运行算法，返回最终社团结果。"""

    def detect_stepwise(self, G: nx.Graph, params: dict[str, Any]) -> Iterator[CommFrame]:
        """逐步产出过程帧（默认仅产出最终一帧，等价不动画）。"""
        res = self.detect(G, params)
        yield CommFrame(
            step=0,
            description="算法完成（该算法暂不支持逐步动画）",
            assignment=res.assignment,
            metric=res.modularity,
        )


class GreedyModularityDetector(CommunityDetector):
    """贪心模块度最大化（Newman 贪心）：从每个节点自成一团开始，反复合并使模块度 Q 增长最快的两团。"""

    name = "modularity"
    display_name = "模块度最大化（贪心）"
    description = (
        "从「每个节点各自一团」出发，每一步合并能让模块度 Q 增长最多的两个社团，直到无法提升。"
    )
    blurb = "最经典的凝聚式算法：直观展示「小团如何一步步并成大社区」，Q 值单调递增。"
    supports_animation = True
    params_schema = [
        {
            "name": "resolution",
            "label": "社团粒度 (resolution)",
            "type": "float",
            "default": 1.0,
            "min": 0.1,
            "max": 5.0,
            "step": 0.1,
            "help": "越大社团越细碎；越小社团越聚合。",
        }
    ]

    def detect(self, G: nx.Graph, params: dict[str, Any]) -> CommResult:
        resolution = float(params.get("resolution", 1.0))
        seq = _greedy_merge_sequence(G, resolution)
        last = seq[-1]
        assign = _pad_isolated(G, last["assignment"])
        compact = _compact_ids(assign)
        comms = _communities_summary(compact)
        q = _safe_modularity(G, _partitions_from_assign(compact))
        return CommResult(
            algorithm=self.name,
            display_name=self.display_name,
            params={"resolution": resolution},
            communities=comms,
            assignment=compact,
            modularity=q,
            supports_animation=self.supports_animation,
        )

    def detect_stepwise(self, G: nx.Graph, params: dict[str, Any]) -> Iterator[CommFrame]:
        resolution = float(params.get("resolution", 1.0))
        seq = _greedy_merge_sequence(G, resolution)
        total = len(seq)
        # 子采样：保留初始 + 均匀间隔 + 最终，控制帧数在 ~25 以内（动画流畅）
        stride = max(1, total // 24)
        keep = set(range(0, total, stride))
        keep.add(total - 1)
        for step, snap in enumerate(seq):
            if step not in keep:
                continue
            compact = _compact_ids(_pad_isolated(G, snap["assignment"]))
            q = snap.get("modularity")
            n_comm = len(set(compact.values()))
            phase = "初始：每个节点独立成团" if step == 0 else f"合并后剩 {n_comm} 个社团"
            yield CommFrame(
                step=step,
                description=f"{phase}（模块度 Q={q:.3f}）" if q is not None else phase,
                assignment=dict(compact),
                metric=q,
            )


class LabelPropagationDetector(CommunityDetector):
    """标签传播（LPA）：节点反复采纳「邻居中最常见的标签」，标签像病毒一样扩散直到稳定。"""

    name = "label_propagation"
    display_name = "标签传播（LPA）"
    description = (
        "每个节点不断改为邻居中出现最多的标签；标签在图上扩散、碰撞，最终同色区域即为一个社团。"
    )
    blurb = "近乎线性的高效算法：动画能直观看到「颜色（标签）如何一层层淹没整张图」。"
    supports_animation = True
    params_schema = [
        {
            "name": "seed",
            "label": "随机种子（可选）",
            "type": "int",
            "default": None,
            "help": "留空则每次结果略有不同；固定种子可复现。",
        }
    ]

    def detect(self, G: nx.Graph, params: dict[str, Any]) -> CommResult:
        rng = _make_rng(params.get("seed"))
        seed = params.get("seed")
        last = None
        for _step, a in _lpa_run(G, rng):
            last = a
        assign = _compact_ids(_pad_isolated(G, last))
        return CommResult(
            algorithm=self.name,
            display_name=self.display_name,
            params={"seed": seed} if seed is not None else {},
            communities=_communities_summary(assign),
            assignment=assign,
            modularity=_safe_modularity(G, _partitions_from_assign(assign)),
            supports_animation=self.supports_animation,
        )

    def detect_stepwise(self, G: nx.Graph, params: dict[str, Any]) -> Iterator[CommFrame]:
        rng = _make_rng(params.get("seed"))
        seq = [(step, _compact_ids(_pad_isolated(G, a))) for step, a in _lpa_run(G, rng)]
        total = len(seq)
        stride = max(1, total // 20)  # 控制帧数在 ~20 以内（动画流畅）
        keep = set(range(0, total, stride)) | {0, total - 1}
        for step, compact in seq:
            if step not in keep:
                continue
            n_comm = len(set(compact.values()))
            if step == 0:
                desc = "初始：每个节点拥有自己的标签（各自一团）"
            else:
                desc = f"第 {step} 轮传播：标签继续扩散（剩 {n_comm} 团）"
            yield CommFrame(step=step, description=desc, assignment=dict(compact), metric=None)


class LouvainDetector(CommunityDetector):
    """Louvain（多级模块度）：先局部优化，再把社团收缩成「超节点」递归，擅长大规模图。"""

    name = "louvain"
    display_name = "Louvain（多级模块度）"
    description = (
        "先让每个节点的局部模块度最优，再把整个社团压缩成一个「超级节点」继续优化，多级递归。"
    )
    blurb = "工业界最常用的算法：动画展示「社团被层层折叠成超级节点」的多级思想。"
    supports_animation = True
    optional_dependency = "community"
    params_schema = [
        {
            "name": "resolution",
            "label": "社团粒度 (resolution)",
            "type": "float",
            "default": 1.0,
            "min": 0.1,
            "max": 5.0,
            "step": 0.1,
            "help": "越大社团越细碎。",
        },
        {
            "name": "randomize",
            "label": "随机化初始化",
            "type": "bool",
            "default": False,
            "help": "开启后每次略有差异。",
        },
    ]

    def detect(self, G: nx.Graph, params: dict[str, Any]) -> CommResult:
        resolution = float(params.get("resolution", 1.0))
        randomize = bool(params.get("randomize", False))
        import community as community_louvain  # lazy：仅 louvain 策略用到

        final = community_louvain.best_partition(
            G, weight="weight", resolution=resolution, randomize=randomize
        )
        assign = _pad_isolated(G, dict(final))
        comms = _communities_summary(assign)
        return CommResult(
            algorithm=self.name,
            display_name=self.display_name,
            params={"resolution": resolution, "randomize": randomize},
            communities=comms,
            assignment=assign,
            modularity=_safe_modularity(G, _partitions_from_assign(assign)),
            supports_animation=self.supports_animation,
        )

    def detect_stepwise(self, G: nx.Graph, params: dict[str, Any]) -> Iterator[CommFrame]:
        resolution = float(params.get("resolution", 1.0))
        randomize = bool(params.get("randomize", False))
        import community as community_louvain  # lazy

        dendro = community_louvain.generate_dendrogram(
            G, weight="weight", resolution=resolution, randomize=randomize
        )
        for step, level in enumerate(dendro):
            assign = _pad_isolated(G, dict(level))
            compact = _compact_ids(assign)
            yield CommFrame(
                step=step,
                description=f"第 {step + 1} 级：社团被折叠为超级节点后重新划分（共 {len(set(compact.values()))} 团）",
                assignment=dict(compact),
                metric=_safe_modularity(G, _partitions_from_assign(compact)),
            )


class InfomapDetector(CommunityDetector):
    """Infomap（信息论）：把「在图上随机游走」描述成信息流，使「描述路径所需的比特数」最小。"""

    name = "infomap"
    display_name = "Infomap（信息流）"
    description = (
        "把图上的随机游走看成信息流，寻找能让「描述一条随机路径所需比特数（码长 L）」最小的划分。"
    )
    blurb = "信息论视角：社团 = 内部随机游走停留久、跨社团跳转少的高密度区域。"
    supports_animation = False  # infomap 包不暴露逐迭代状态，故不提供逐步动画
    optional_dependency = "infomap"
    params_schema = [
        {
            "name": "seed",
            "label": "随机种子（可选）",
            "type": "int",
            "default": None,
            "help": "留空随机。",
        },
        {
            "name": "two_level",
            "label": "仅两级优化",
            "type": "bool",
            "default": False,
            "help": "关闭则做完整多级搜索（更准但更慢）。",
        },
    ]

    def detect(self, G: nx.Graph, params: dict[str, Any]) -> CommResult:
        import infomap  # lazy

        seed = params.get("seed")
        two_level = bool(params.get("two_level", False))
        # infomap 要求整数节点 id：做 0..n-1 重标号；seed 必须 >= 1
        nodes = list(G.nodes())
        idx = {n: i for i, n in enumerate(nodes)}
        im = infomap.Infomap(
            "--two-level" if two_level else "",
            seed=seed if seed is not None else 1,
        )
        for u, v in G.edges():
            im.add_link(idx[u], idx[v], G[u][v].get("weight", 1))
        result = im.run()
        # infomap 2.x：优先用 run() 返回的 Result.modules()；老版本退回 get_modules()
        try:
            mod_map = result.modules()
        except AttributeError:
            mod_map = im.get_modules()
        assign: dict[str, int] = {}
        for nid, mod in mod_map.items():
            assign[nodes[int(nid)]] = int(mod)
        assign = _pad_isolated(G, assign)
        assign = _compact_ids(assign)
        comms = _communities_summary(assign)
        return CommResult(
            algorithm=self.name,
            display_name=self.display_name,
            params={"seed": seed, "two_level": two_level},
            communities=comms,
            assignment=assign,
            modularity=_safe_modularity(G, _partitions_from_assign(assign)),
            supports_animation=self.supports_animation,
            frames=[],
        )


class GirvanNewmanDetector(CommunityDetector):
    """Girvan-Newman（边中介度分裂）：反复删除「最像桥梁的边」，图被一刀刀切开成社团。"""

    name = "girvan_newman"
    display_name = "Girvan-Newman（边中介度分裂）"
    description = (
        "反复计算边的中介中心性、删掉「最像桥梁」的边，图被一步步切断，直到达到目标社团数。"
    )
    blurb = "与凝聚式相反的自顶向下思路：直观看到「先断哪座桥，整张图才裂开」。高亮边即被切断的桥。"
    supports_animation = True
    params_schema = [
        {
            "name": "target_communities",
            "label": "目标社团数",
            "type": "int",
            "default": 6,
            "min": 2,
            "max": 20,
            "step": 1,
            "help": "分裂到该数量社团即停止（也受最大步数限制）。",
        }
    ]

    def detect(self, G: nx.Graph, params: dict[str, Any]) -> CommResult:
        target = int(params.get("target_communities", 6))
        from networkx.algorithms.community import girvan_newman

        generator = girvan_newman(G)
        partitions: list[set] = [set(G.nodes())]
        max_steps = 40
        for step, comms in enumerate(generator):
            partitions = [set(c) for c in comms]
            if len(partitions) >= target or step >= max_steps - 1:
                break
        assign = _pad_isolated(G, _assignment_from_partitions(partitions))
        return CommResult(
            algorithm=self.name,
            display_name=self.display_name,
            params={"target_communities": target},
            communities=_communities_summary(assign),
            assignment=assign,
            modularity=_safe_modularity(G, partitions),
            supports_animation=self.supports_animation,
        )

    def detect_stepwise(self, G: nx.Graph, params: dict[str, Any]) -> Iterator[CommFrame]:
        target = int(params.get("target_communities", 6))
        from networkx.algorithms.community import girvan_newman

        generator = girvan_newman(G)
        max_steps = 40
        for step, comms in enumerate(generator):
            partitions = [set(c) for c in comms]
            assign = _pad_isolated(G, _assignment_from_partitions(partitions))
            compact = _compact_ids(assign)
            # 高亮：跨社团的「桥边」（即被切断的边）
            cut_edges = [(u, v) for u, v in G.edges() if compact.get(u) != compact.get(v)]
            q = _safe_modularity(G, partitions)
            # 退化图（如无边 / 全孤立节点）下 _safe_modularity 返回 None，直接格式化会
            # 抛 TypeError（同文件贪心策略 194 行已按此模式保护，此处是遗漏）。
            desc = (
                f"第 {step + 1} 刀：删除最高中介度的桥边，裂成 {len(partitions)} 个社团（Q={q:.3f}）"
                if q is not None
                else f"第 {step + 1} 刀：裂成 {len(partitions)} 个社团（该图无法计算模块度）"
            )
            yield CommFrame(
                step=step,
                description=desc,
                assignment=dict(compact),
                metric=q,
                highlight_edges=cut_edges,
            )
            if len(partitions) >= target or step >= max_steps - 1:
                break


# ----------------------------- 注册表与编排 -----------------------------

DETECTORS: dict[str, type[CommunityDetector]] = {
    d.name: d
    for d in (
        GreedyModularityDetector,
        LabelPropagationDetector,
        LouvainDetector,
        InfomapDetector,
        GirvanNewmanDetector,
    )
}


def get_detector(name: str) -> CommunityDetector:
    """按名称取策略实例；未知算法抛 ValueError（由路由转 400）。"""
    cls = DETECTORS.get(name)
    if cls is None:
        raise ValueError(f"未知社区分析算法：{name!r}；可选：{', '.join(sorted(DETECTORS))}")
    return cls()


def list_detectors() -> list[dict[str, Any]]:
    """算法目录：前端据此动态生成下拉与参数表单。"""
    return [
        {
            "name": d.name,
            "display_name": d.display_name,
            "description": d.description,
            "blurb": d.blurb,
            "supports_animation": d.supports_animation,
            "available": d.available(),
            "optional_dependency": d.optional_dependency,
            "params_schema": d.params_schema,
        }
        for d in DETECTORS.values()
    ]


def run_detection(
    algorithm: str,
    G: nx.Graph,
    params: dict[str, Any] | None = None,
    animate: bool = False,
) -> CommResult:
    """编排入口：选策略 → 校验可用性 → 运行（按需产出动画帧）。

    缺失可选依赖时抛 RuntimeError（算法不可用），由路由转清晰的 400。
    """
    detector = get_detector(algorithm)
    if not detector.available():
        dep = detector.optional_dependency
        raise RuntimeError(f"算法「{detector.display_name}」需要可选依赖：pip install {dep}")
    params = params or {}
    res = detector.detect(G, params)
    if animate and detector.supports_animation:
        res.frames = list(detector.detect_stepwise(G, params))
    return res


# ----------------------------- 分析范围（异质 vs 同类） -----------------------------
# 节点类型词汇表（game/goty/genre/studio/award 及各自物理含义）已外置到
# ``api/schema.GRAPH_SCHEMA``（单一事实来源）。本模块不再硬编码业务术语，
# 分析范围目录改由 schema.list_scopes() 派生。异质/同类物理含义差异的详细说明见 schema.py。


# ----------------------------- 内部工具 -----------------------------
def _safe_modularity(
    G: nx.Graph, partitions: list[set], resolution: float = 1.0, weight: str = "weight"
) -> float | None:
    """计算模块度 Q（partition 为空或单团时为 0）。加权图按 ``weight`` 取边权。"""
    try:
        if not partitions or len(partitions) <= 1:
            return 0.0
        return float(modularity(G, partitions, resolution=resolution, weight=weight))
    except Exception:  # 个别退化图无法算 Q 时不阻塞
        return None


def _greedy_merge_sequence(
    G: nx.Graph, resolution: float = 1.0, weight: str = "weight"
) -> list[dict[str, Any]]:
    """贪心模块度合并：返回每一步快照 [{step, assignment, modularity}]，用于教育性动画。

    从「每节点独立成团」出发，每步合并使 ΔQ 最大的两团：

        ΔQ(C,D) = E_CD/(2m) − ρ · T_C·T_D / (4 m²)

    其中 m=边权总和，E_CD=C/D 间边权之和，T_C=C 的加权度数之和，ρ=resolution。
    该式与 networkx ``greedy_modularity_communities`` 的合并判据一致（仅整体缩放，
    不影响「选哪一对」与「何时停止」），故对无权图最终划分与 networkx 完全相同。
    以「社区间边表 E」为唯一真相源（不另维护邻接表，避免二者失同步），每步扫描 E 选最优对；
    合并直到 E 清空（每个连通分量收成一个社团）或 ΔQ≤0。最末帧即算法最终划分。
    """
    m = sum(G[u][v].get(weight, 1) for u, v in G.edges())  # 边权总和
    if m == 0:  # 无边图
        assign = {n: n for n in G.nodes()}
        return [{"step": 0, "assignment": assign, "modularity": 0.0}]

    T = dict(G.degree(weight=weight))  # 加权度数（社区总度数用代表节点标识）
    members = {n: {n} for n in G.nodes()}
    E: dict[tuple, float] = {}

    def ekey(c: Any, d: Any) -> tuple:
        return (c, d) if c < d else (d, c)

    for u, v in G.edges():
        k = ekey(u, v)
        E[k] = E.get(k, 0) + G[u][v].get(weight, 1)
    reps = set(G.nodes())
    seq = [_snap(0, {n: n for n in G.nodes()}, G, resolution, weight)]

    while E:  # 仍有社区间边 → 仍可合并
        best_c = best_d = None
        best_val = None
        for (c, d), wt in E.items():
            val = wt / (2 * m) - resolution * T[c] * T[d] / (4 * m * m)
            if best_val is None or val > best_val:
                best_val, best_c, best_d = val, c, d
        if best_c is None:
            break
        if best_val <= 0:
            # 贪心模块度：当所有合并都不再提升 Q 时停止（与 networkx 语义一致）
            break
        c, d = best_c, best_d
        E.pop((c, d), 0)  # c,d 间边变为内部边
        T[c] += T[d]
        members[c] |= members[d]
        # 把 d 的其余社区间边并入 c
        for x, y in list(E.keys()):
            if x != d and y != d:
                continue
            if x == d and y == d:  # d 内部自环，忽略
                E.pop((x, y), None)
                continue
            other = y if x == d else x
            w2 = E.pop((x, y))
            nk = ekey(c, other)
            E[nk] = E.get(nk, 0) + w2
        reps.discard(d)
        assign = {n: r for r in reps for n in members[r]}
        seq.append(_snap(len(seq), assign, G, resolution, weight))
    return seq


def _lpa_run(G: nx.Graph, rng, max_iter: int = 50, weight: str = "weight"):
    """标签传播（LPA）同步迭代：yield (step, assignment)。初始每节点独立标签，直到稳定。

    邻居标签按边权加权计数——投影图上「共享属性更多」的邻居影响更大，结果更稳健。
    """
    assign = {n: i for i, n in enumerate(G.nodes())}
    yield 0, dict(assign)
    for it in range(1, max_iter + 1):
        changed = False
        order = list(G.nodes())
        rng.shuffle(order)
        for n in order:
            nbrs = list(G.neighbors(n))
            if not nbrs:
                continue
            counts: dict[int, float] = {}
            for m in nbrs:
                w = G[n][m].get(weight, 1)
                counts[assign[m]] = counts.get(assign[m], 0) + w
            best = max(counts.items(), key=lambda kv: (kv[1], rng.random()))[0]
            if assign[n] != best:
                assign[n] = best
                changed = True
        yield it, dict(assign)
        if not changed:
            break


def _snap(
    step: int, assign: dict[str, Any], G: nx.Graph, resolution: float, weight: str = "weight"
) -> dict[str, Any]:
    """把一个 assignment 快照转成 {step, assignment, modularity}（Q 由 networkx 计算，作为真值）。"""
    by_comm: dict[Any, set] = {}
    for nid, c in assign.items():
        by_comm.setdefault(c, set()).add(nid)
    return {
        "step": step,
        "assignment": assign,
        "modularity": _safe_modularity(
            G, list(by_comm.values()), resolution=resolution, weight=weight
        ),
    }


def _partitions_from_assign(assign: dict[str, int]) -> list[set]:
    by_comm: dict[int, set] = {}
    for nid, c in assign.items():
        by_comm.setdefault(c, set()).add(nid)
    return list(by_comm.values())


def _compact_ids(assign: dict[str, int]) -> dict[str, int]:
    """把社团 id 重映射为 0..k-1 紧凑编号（保持稳定可读的着色）。"""
    remap: dict[int, int] = {}
    out: dict[str, int] = {}
    for nid, c in assign.items():
        if c not in remap:
            remap[c] = len(remap)
        out[nid] = remap[c]
    return out


def _make_rng(seed):
    import random

    return random.Random(seed)
