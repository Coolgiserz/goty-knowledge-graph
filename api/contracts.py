"""社区发现结果的**稳定中间契约**（与具体算法库解耦）。

为什么需要这一层
----------------
前端此前直接消费算法库适配器拼出的字典，隐含了「所有算法都用模块度 Q 衡量好坏」的假设：

- 顶层只有固定的 ``modularity`` 字段 —— Infomap 的核心指标 ``codelength``（编码长度，
  越小越好）**无处安放、被直接丢弃**；
- 动画帧里只有裸数值 ``metric``，前端只能硬编码文案 ``模块度 Q=...``，
  一旦指标不是模块度就会**张冠李戴**。

这把前端和「当前这批算法库」焊死了：换库（networkx → igraph / leiden）、加算法、
改指标，都要动前端。

本模块定义**只描述领域事实、不绑定任何算法库**的契���，由适配器把各家库的输出翻译过来。
未来切换算法库时，只需新增一个产出本契约的适配器，前端零改动。

设计要点
--------
- **指标是「列表 + 语义标签」而非固定字段**：``metrics: list[QualityMetric]``，
  每项自带 ``label``（中文名）与 ``higher_is_better``（方向），前端按标签渲染，
  不再硬编码「模块度」。
- **向后兼容**：仍下发顶层 ``modularity``（从 metrics 派生）与帧内数值 ``metric``，
  现有前端无需改动即可继续工作；新前端可改用 ``metrics`` / ``metric_label``。
- **顶层下发 ``assignment``**：此前前端需从 ``communities[].members`` 反推归属，
  现在直接给全，减少一次派生逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class QualityMetric:
    """一项**质量指标**（算法无关）。

    不同算法优化的目標不同，故指标不止一种：

    - ``modularity`` 模块度 Q（越大越好，[-0.5, 1]）——贪心 / Louvain / LPA / GN 通用；
    - ``codelength`` 编码长度（**越小越好**，Map Equation）——Infomap 专用；
    - 未来的 Leiden（``quality``）、基于显著性的指标等亦可接入。

    ``higher_is_better`` 让前端能正确渲染「越高越好 / 越低越好」的箭头与配色，
    而不必知道具体是哪种算法。
    """

    key: str
    label: str
    value: float | None
    higher_is_better: bool = True
    precision: int = 3
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "higher_is_better": self.higher_is_better,
            "precision": self.precision,
            "note": self.note,
        }

    def format(self) -> str:
        """给前端/日志用的可读文本（如「模块度 Q=0.618」）。"""
        if self.value is None:
            return f"{self.label}=—"
        return f"{self.label}={self.value:.{self.precision}f}"


# 常用指标的语义定义（集中一处，避免各适配器各写一套中文名与方向）
MODULARITY = QualityMetric(
    key="modularity",
    label="模块度 Q",
    value=None,
    higher_is_better=True,
    precision=3,
    note="社区内部连边密度相对随机图的优势，越大越好（一般 >0.3 视为结构明显）",
)

CODELENGTH = QualityMetric(
    key="codelength",
    label="编码长度",
    value=None,
    higher_is_better=False,  # Map Equation：描述随机游走所需比特数，越小越好
    precision=4,
    note="Map Equation 目标：描述图上随机游走路径所需的信息量，越小越好",
)


@dataclass(frozen=True)
class CommunityFrame:
    """算法过程的一帧快照（教育性动画用），与具体算法无关。"""

    step: int
    description: str
    assignment: dict[str, int]
    metric: QualityMetric | None = None
    highlight_edges: list[tuple[str, str]] = field(default_factory=list)
    highlight_nodes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "description": self.description,
            "assignment": self.assignment,
            # 数值仍下发（向后兼容旧前端）；语义标签单独给，避免旧前端把
            # codelength 错标成「模块度」。
            "metric": self.metric.value if self.metric else None,
            "metric_label": self.metric.label if self.metric else None,
            "metric_higher_is_better": (self.metric.higher_is_better if self.metric else None),
            "highlight_edges": [list(e) for e in self.highlight_edges],
            "highlight_nodes": list(self.highlight_nodes),
        }


@dataclass(frozen=True)
class CommunityDetectionResult:
    """社区发现的**稳定契约结果**：只描述领域事实，不绑定任何算法库。

    各算法库（networkx / python-louvain / infomap / 未来的 igraph、leiden…）的适配器
    都应产出本结构；路由与前端只依赖它，从而在换库时保持稳定。
    """

    algorithm: str  # 稳定的算法标识（领域名，不随底层库更换而改变）
    display_name: str
    params: dict[str, Any]
    communities: list[dict[str, Any]]  # [{id, size, members}]，按规模降序
    assignment: dict[str, int]  # node_id -> community_id（顶层下发，前端无需派生）
    metrics: list[QualityMetric] = field(default_factory=list)
    supports_animation: bool = False
    frames: list[CommunityFrame] = field(default_factory=list)

    @property
    def primary_metric(self) -> QualityMetric | None:
        """主指标（列表首项）；无指标时返回 None。"""
        return self.metrics[0] if self.metrics else None

    def metric(self, key: str) -> QualityMetric | None:
        """按 key 取指标（如 ``"modularity"``）；不存在返回 None。"""
        for m in self.metrics:
            if m.key == key:
                return m
        return None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 API 响应（顶层字段由 store / 路由补充，见 graph_store.communities）。"""
        modularity = self.metric("modularity")
        return {
            "algorithm": self.algorithm,
            "display_name": self.display_name,
            "params": self.params,
            "communities": self.communities,
            # 顶层下发归属：此前前端需从 communities[].members 反推
            "assignment": self.assignment,
            # 指标列表（推荐消费方式）：自带语义标签与方向
            "metrics": [m.to_dict() for m in self.metrics],
            # 向后兼容：旧前端直接读 modularity / frames[].metric 仍可用
            "modularity": modularity.value if modularity else None,
            "supports_animation": self.supports_animation,
            "frames": [f.to_dict() for f in self.frames],
        }


def from_legacy(algorithm: str, display_name: str, res: Any) -> CommunityDetectionResult:
    """适配器：把现有的 ``api.community.CommResult`` 翻译成稳定契约。

    这是**迁移期**的桥接——现有 5 个 detector 仍输出 ``CommResult``（含裸 ``modularity``），
    经本函数统一成契约。将来新增/更换算法库时，直接产出 ``CommunityDetectionResult``
    即可，无需再走这里。
    """
    from dataclasses import replace

    metrics: list[QualityMetric] = []
    q = getattr(res, "modularity", None)
    if q is not None:
        metrics.append(replace(MODULARITY, value=float(q)))

    frames = []
    for f in getattr(res, "frames", []) or []:
        raw_metric = getattr(f, "metric", None)
        frames.append(
            CommunityFrame(
                step=getattr(f, "step", 0),
                description=getattr(f, "description", ""),
                assignment=dict(getattr(f, "assignment", {}) or {}),
                metric=replace(MODULARITY, value=float(raw_metric))
                if raw_metric is not None
                else None,
                highlight_edges=[tuple(e) for e in getattr(f, "highlight_edges", []) or []],
                highlight_nodes=list(getattr(f, "highlight_nodes", []) or []),
            )
        )

    return CommunityDetectionResult(
        algorithm=algorithm,
        display_name=display_name,
        params=dict(getattr(res, "params", {}) or {}),
        communities=list(getattr(res, "communities", []) or []),
        assignment=dict(getattr(res, "assignment", {}) or {}),
        metrics=metrics,
        supports_animation=bool(getattr(res, "supports_animation", False)),
        frames=frames,
    )
