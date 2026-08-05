"""年度最佳游戏知识图谱 · 数据挖掘工具包（可插拔架构）。

包结构：
  config.py        —— 所有超参数集中于此（改一处即可调全链路）
  constants.py     —— 输出文件名（PNG / 报告）常量
  io_utils.py      —— 图谱加载、共享计算（去重）
  context.py       —— PipelineContext：在阶段间传递图 / DataFrame / artifacts
  features.py      —— FeatureEngine + 可注册特征组（Strategy）
  clusterers.py    —— 聚类算法策略注册表（Strategy）
  analyzers.py     —— Analyzer 基类 + 注册表（聚类 / 社区 / 热点）
  visualizers.py   —— Visualizer 基类 + 注册表（7 张图）
  pipeline.py      —— run_pipeline：编排全部阶段
  run_ml.py        —— 瘦 CLI 入口

设计模式：
  - Strategy（策略）：聚类算法、特征组均可替换
  - Registry（注册表）：特征组 / 聚类器 / Analyzer / Visualizer 均通过注册表发现
  - Template / Pipeline：run_pipeline 统一串联「计算 → 落盘 → 可视化 → 报告」
  - Context Object：阶段间通过内存上下文传递数据，去除模块间的磁盘耦合
"""
