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
    """社区发现（可插拔：louvain | infomap | walktrap）。

    method 选择主方法（注册表键，见 community.py）；
    Infomap 与 Walktrap 作为补充方法始终尝试（依赖免费），三者并列对照。
    """
    method: str = "louvain"          # 主方法：louvain | infomap | walktrap
    resolution: float = 1.0          # Louvain 分辨率（越大社区越碎）
    seed: int = 42
    num_trials: int = 20             # Infomap 随机游走重复次数（越多越稳定）
    walktrap_steps: int = 4          # Walktrap 随机游走步数（越大越全局）


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
class RandomWalkConfig:
    """异构随机游走嵌入（用于工作室风格空间与图嵌入）。

    在完整异构图（游戏↔类型↔工作室↔奖项）上做截断随机游走，
    统计游戏节点共现 -> 共现矩阵(log1p) -> TruncatedSVD 降维得到游戏嵌入。
    随机游走捕捉二阶/多跳邻近性，比直接共享类型(最短路径)更贴近直觉。
    """
    num_walks: int = 25          # 每个游戏节点起始的游走次数
    walk_len: int = 40           # 每次游走步数
    window: int = 5              # 共现上下文窗口
    embed_dim: int = 24          # SVD 嵌入维度
    seed: int = 42


@dataclass
class GotyAffinityConfig:
    """GOTY 品味网络（个性化随机游走 / Personalized PageRank）。

    从全部 GOTY 获奖作(种子)做个性化 PageRank，衡量非获奖作/工作室
    与「年度最佳品味」的亲和度，得到「喜欢 GOTY 的人也会喜欢…」推荐。
    """
    alpha: float = 0.85           # PageRank 阻尼系数
    top_n: int = 10               # 推荐/榜单展示条数


@dataclass
class MLConfig:
    """总配置。"""
    design_dims: frozenset = frozenset({"开放世界", "多人合作", "在线"})
    game_graph: GameGraphConfig = field(default_factory=GameGraphConfig)
    features: FeatureConfig = field(default_factory=FeatureConfig)
    cluster: ClusterConfig = field(default_factory=ClusterConfig)
    community: CommunityConfig = field(default_factory=CommunityConfig)
    hotspot: HotspotConfig = field(default_factory=HotspotConfig)
    random_walk: RandomWalkConfig = field(default_factory=RandomWalkConfig)
    goty_affinity: GotyAffinityConfig = field(default_factory=GotyAffinityConfig)
    random_state: int = 42
