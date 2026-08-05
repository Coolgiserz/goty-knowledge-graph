"""PipelineContext：在阶段之间传递数据，去除模块间的磁盘耦合。

旧实现中 factors → clusters → visualize 通过磁盘上的 CSV/JSON 文件传递，
文件名写死、易错位。这里改为在内存上下文(ctx)中共享：
  - 图结构、游戏节点、类型名、工作室名、投影图（只构建一次）
  - artifacts：各阶段产出（DataFrame / dict），按 key 存取
  - report：markdown 片段累加
"""
from .config import MLConfig
from .io_utils import (
    load_graph, ensure_out, DEFAULT_GRAPH_PATH, DEFAULT_OUT_DIR,
    game_nodes, genre_names, studio_names, build_game_graph, build_full_nx,
)


class PipelineContext:
    def __init__(self, config: MLConfig = None, graph_path: str = None, out_dir: str = None):
        self.config = config or MLConfig()
        self.graph = load_graph(graph_path or DEFAULT_GRAPH_PATH)
        self.out_dir = ensure_out(out_dir or DEFAULT_OUT_DIR)

        # 只构建一次、处处复用
        self.games = game_nodes(self.graph)
        self.genre_names = genre_names(self.graph, self.config.design_dims)
        self.studio_names = studio_names(self.graph)
        self.GG = build_game_graph(self.graph, self.config)
        # 完整异构图（游戏/工作室/类型/奖项 混合节点）：供随机游走嵌入与个性化 PageRank 使用
        self.G_full = build_full_nx(self.graph)

        self.artifacts: dict = {}
        self.report: list = []

    # ---- artifacts 存取 ----
    def add(self, key: str, obj):
        self.artifacts[key] = obj
        return obj

    def get(self, key: str):
        return self.artifacts.get(key)

    # ---- report 累加 ----
    def write(self, *lines: str):
        for s in lines:
            self.report.append(s)
