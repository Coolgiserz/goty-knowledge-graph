"""数据挖掘入口（瘦 CLI）。

用法：
  python analysis/run_ml.py                         # 默认：KMeans + PCA
  python analysis/run_ml.py --clusterer spectral    # 换成谱聚类
  python analysis/run_ml.py --no-pca                # 关掉 PCA 预处理
  python analysis/run_ml.py --exclude-reputation    # 关掉 studio_wins（防标签泄漏）
  python analysis/run_ml.py --k 6                   # 固定 k，跳过选 k
  python analysis/run_ml.py --graph data/graph.json --out analysis/output

依赖：analysis/requirements.txt（numpy/pandas/scikit-learn/networkx/matplotlib/scipy）。
建议在隔离 venv 中运行（见 Makefile 的 `make analysis`）。
"""
import os
import sys
import argparse

# 让 `ml` 作为包可被导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ml.config import MLConfig, ClusterConfig, FeatureConfig
from ml.pipeline import run_pipeline


def main():
    ap = argparse.ArgumentParser(description="GOTY 知识图谱 · 数据挖掘")
    ap.add_argument("--clusterer", default=None,
                    help="聚类算法：kmeans | hierarchical | spectral | dbscan")
    ap.add_argument("--no-pca", action="store_true", help="关闭 PCA 预处理")
    ap.add_argument("--k", type=int, default=None, help="固定 k（跳过选 k）")
    ap.add_argument("--exclude-reputation", action="store_true",
                    help="关闭 studio_wins 特征（防止 is_goty 标签泄漏）")
    ap.add_argument("--graph", default=None, help="graph.json 路径")
    ap.add_argument("--out", default=None, help="输出目录")
    args = ap.parse_args()

    cfg = MLConfig()
    if args.clusterer:
        cfg.cluster.method = args.clusterer
    if args.no_pca:
        cfg.cluster.use_pca = False
    if args.k is not None:
        cfg.cluster.fixed_k = args.k
    if args.exclude_reputation:
        cfg.features.include_studio_wins = False

    run_pipeline(cfg, args.graph, args.out)


if __name__ == "__main__":
    main()
