"""集中配置：所有可调超参数都放在这里，改一处即可影响全链路。

每个子配置对应链路中的一个阶段，便于「可插拔 / 可拓展」——
例如想换聚类算法改 ClusterConfig.method，想关掉声誉特征改 FeatureConfig。
"""
from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass
class GameGraphConfig:
    """游戏-游戏相似投影图的连边权重。"""
    genre_weight: float = 1.0          # 共享一个玩法类型 +genre_weight
    studio_weight: float = 4.0         # 同工作室 +studio_weight
    exclude_design_dims: bool = True   # 设计维度(开放世界/合作/在线)不作为连边，避免巨型团


@dataclass
class FeatureConfig:
    """特征工程开关。"""
    # 启用的特征组（按 features.py 中的注册名）。顺序即列顺序。
    groups: Tuple[str, ...] = ("topology", "attributes", "reputation", "genre_onehot")
    impute_rating: str = "median"      # player_rating 缺失填补：median | mean | zero
    include_studio_wins: bool = True   # 工作室夺冠数（由 is_goty 派生）——存在标签泄漏，可关闭


@dataclass
class ClusterConfig:
    """聚类阶段。"""
    method: str = "kmeans"             # 注册表键：kmeans | hierarchical | spectral | dbscan
    k_range: Tuple[int, int] = (2, 9)  # 轮廓系数选 k 的搜索区间（闭区间）
    fixed_k: Optional[int] = None      # 非 None 时跳过选 k，直接用该值
    random_state: int = 42
    scale: bool = True                 # StandardScaler 标准化
    use_pca: bool = True               # PCA 白化后再聚类（缓解高维 one-hot 的维度灾难）
    pca_variance: float = 0.95         # use_pca 时保留的方差比例
    warn_silhouette: float = 0.25      # 轮廓低于此值视为弱划分，报告内提示


@dataclass
class CommunityConfig:
    """社区发现（Louvain）。"""
    resolution: float = 1.0
    seed: int = 42


@dataclass
class HotspotConfig:
    """时代热点统计。"""
    # (起始年, 结束年, 名称)
    eras: Tuple[Tuple[int, int, str], ...] = (
        (2006, 2010, "2006-2010"),
        (2011, 2015, "2011-2015"),
        (2016, 2020, "2016-2020"),
        (2021, 2025, "2021-2025"),
    )
    rolling_window: int = 5            # 工作室滚动热度窗口（年）


@dataclass
class MLConfig:
    """总配置。"""
    design_dims: frozenset = frozenset({"开放世界", "多人合作", "在线"})
    game_graph: GameGraphConfig = field(default_factory=GameGraphConfig)
    features: FeatureConfig = field(default_factory=FeatureConfig)
    cluster: ClusterConfig = field(default_factory=ClusterConfig)
    community: CommunityConfig = field(default_factory=CommunityConfig)
    hotspot: HotspotConfig = field(default_factory=HotspotConfig)
    random_state: int = 42
