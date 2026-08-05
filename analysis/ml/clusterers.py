"""聚类算法策略（Strategy 模式）。

每个算法是一个 Clusterer 子类，通过 @Clusterer.register 注册；
ClusterAnalyzer 按配置中的 method 名选取，无需改分析逻辑即可切换算法。

为何提供多种算法（方法选择）：
  - KMeans：球形簇、快；但对高维稀疏 one-hot 与不规则形状敏感（原实现弱点）。
  - Hierarchical(Ward)：层次结构、可解释；同样偏球形。
  - Spectral：基于图拉普拉斯，能捕捉非凸结构，适合「玩法家族」这类流形。
  - DBSCAN：无需预设 k、可识别噪声点；但 eps 需调参，样本少时不稳定。

默认仍用 KMeans，但先用 PCA 白化（见 ClusterAnalyzer）以缓解维度灾难。
"""
from sklearn.cluster import KMeans, AgglomerativeClustering, SpectralClustering, DBSCAN


class Clusterer:
    name = "base"
    _registry: dict = {}

    def __init__(self, config):
        self.config = config

    def needs_k(self) -> bool:
        """是否需要先选 k（DBSCAN 不需要）。"""
        return True

    def fit_predict(self, X, k: int = None):
        raise NotImplementedError

    @classmethod
    def register(cls, sub):
        cls._registry[sub.name] = sub
        return sub

    @classmethod
    def get(cls, name: str) -> "Clusterer":
        if name not in cls._registry:
            raise ValueError(f"未知聚类算法：{name}；可用：{list(cls._registry)}")
        return cls._registry[name]


@Clusterer.register
class KMeansClusterer(Clusterer):
    name = "kmeans"

    def fit_predict(self, X, k: int = None):
        km = KMeans(n_clusters=k, random_state=self.config.random_state, n_init=10)
        return km.fit_predict(X)


@Clusterer.register
class HierarchicalClusterer(Clusterer):
    name = "hierarchical"

    def fit_predict(self, X, k: int = None):
        return AgglomerativeClustering(n_clusters=k).fit_predict(X)


@Clusterer.register
class SpectralClusterer(Clusterer):
    name = "spectral"

    def fit_predict(self, X, k: int = None):
        n_neigh = min(10, max(2, len(X) - 1))
        return SpectralClustering(
            n_clusters=k, random_state=self.config.random_state,
            affinity="nearest_neighbors", n_neighbors=n_neigh,
        ).fit_predict(X)


@Clusterer.register
class DBSCANClusterer(Clusterer):
    name = "dbscan"

    def needs_k(self) -> bool:
        return False

    def fit_predict(self, X, k: int = None):
        # eps 为启发式默认值；样本量小、维度高时建议用 --no-pca 或调参
        return DBSCAN(eps=0.5, min_samples=3).fit_predict(X)
