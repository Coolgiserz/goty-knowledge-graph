"""输出文件名常量（PNG / CSV / JSON）。

集中管理，避免 analyzer 与 visualizer 之间因字符串不一致而错位。
"""

# 与旧版保持一致的文件名，便于后续接入网站 / 历史对比
CSV_FACTORS = "factors.csv"
CSV_CLUSTERS = "clusters.csv"
CSV_COMMUNITIES = "communities.csv"
CSV_HOTSPOT_ERA = "hotspot_era.csv"

JSON_FACTOR_DOC = "factor_doc.json"
JSON_CLUSTER_PROFILE = "cluster_profile.json"
JSON_COMMUNITY_PROFILE = "community_profile.json"
JSON_HOTSPOT_SUMMARY = "hotspot_summary.json"
MD_REPORT = "ML_REPORT.md"

# PNG 文件名（analyzer 在报告中引用，visualizer 负责生成）
PNG = {
    "factor_corr": "factor_correlation.png",
    "k_silhouette": "k_silhouette.png",
    "cluster_pca": "cluster_pca.png",
    "cluster_profile": "cluster_profile.png",
    "community": "community_graph.png",
    "hotspot": "hotspot_trend.png",
    "centrality": "centrality_top.png",
}
