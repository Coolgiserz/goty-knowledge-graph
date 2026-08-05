"""输出文件名常量（PNG / CSV / JSON）。

集中管理，避免 analyzer 与 visualizer 之间因字符串不一致而错位。
"""

# 与旧版保持一致的文件名，便于后续接入网站 / 历史对比
CSV_FACTORS = "factors.csv"
CSV_CLUSTERS = "clusters.csv"
CSV_COMMUNITIES = "communities.csv"
CSV_HOTSPOT_ERA = "hotspot_era.csv"
CSV_HOTSPOT_YEAR = "hotspot_year.csv"
CSV_STUDIO_SIM = "studio_similarity.csv"          # 最短路径（图谱距离）工作室相似度矩阵
CSV_STUDIO_SIM_RW = "studio_similarity_rw.csv"     # 随机游走嵌入 工作室相似度矩阵
CSV_STUDIO_STYLE = "studio_style.csv"              # 最短路径 MDS 风格散点坐标
CSV_STUDIO_STYLE_RW = "studio_style_rw.csv"        # 随机游走 MDS 风格散点坐标
CSV_GOTY_GENRE = "goty_genre.csv"
CSV_COMMUNITIES_IM = "communities_infomap.csv"
CSV_COMMUNITIES_WT = "communities_walktrap.csv"
CSV_GOTY_AFFINITY = "goty_affinity.csv"

JSON_FACTOR_DOC = "factor_doc.json"
JSON_CLUSTER_PROFILE = "cluster_profile.json"
JSON_COMMUNITY_PROFILE = "community_profile.json"
JSON_HOTSPOT_SUMMARY = "hotspot_summary.json"
JSON_STUDIO_STYLE = "studio_style.json"
JSON_GOTY_PROFILE = "goty_profile.json"
JSON_COMMUNITY_PROFILE_IM = "community_profile_infomap.json"
JSON_COMMUNITY_PROFILE_WT = "community_profile_walktrap.json"
JSON_GOTY_AFFINITY = "goty_affinity.json"
MD_REPORT = "ML_REPORT.md"

# PNG 文件名（analyzer 在报告中引用，visualizer 负责生成）
PNG = {
    "factor_corr": "factor_correlation.png",
    "k_silhouette": "k_silhouette.png",
    "cluster_pca": "cluster_pca.png",
    "cluster_profile": "cluster_profile.png",
    "community": "community_graph.png",
    "community_infomap": "community_infomap.png",
    "community_walktrap": "community_walktrap.png",
    "hotspot": "hotspot_trend.png",
    "centrality": "centrality_top.png",
    "studio_sim": "studio_similarity_heatmap.png",
    "studio_sim_rw": "studio_similarity_rw_heatmap.png",
    "studio_style_scatter": "studio_style_scatter.png",
    "studio_style_rw_scatter": "studio_style_rw_scatter.png",
    "goty_distinguish": "goty_distinguish.png",
    "goty_genre": "goty_genre_overindex.png",
    "goty_affinity": "goty_affinity.png",
}
