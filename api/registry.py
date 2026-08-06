"""探索板块注册表（与 analysis/ml 同一套「可插拔 / 注册表」思想）。

新增一个可交互的探索板块 = 写一个 ExplorationTool 子类并 @register。
每个工具声明：参数 schema、默认参数下的解读文本、解读默认值，
并在 run(params) 中复用 analysis/ml 的计算函数，返回可视化面板 + 表格 + 指标。

有效性判定（核心需求：区分数据 / 解读有效性）：
  - data_matches_baseline：底层数据是否与「文档快照基线」一致
    （来自 graph.json 的 sha256 守卫，数据漂移则全部预写解读失效）
  - interpretation_valid：当前参数是否仍在解读默认值范围内
    （用户调节了会改变结论的参数 → 预写解读失效，前端置灰）
"""
from .models import coerce_params

_REGISTRY = {}


class ExplorationTool:
    name = ""
    label = ""
    description = ""
    params = []                    # [ParamSpec]
    interpretation = ""           # 默认参数下的解读（markdown）
    interpretation_defaults = {}   # {key: value} 解读所假设的默认参数

    def run(self, params):
        """传入已校验的参数 dict，返回
        {panels:[{type,title,data}], tables:[{title,columns,rows}], metrics:{}}。"""
        raise NotImplementedError

    def validity(self, params, data_matches_baseline):
        invalid = []
        for k, dv in self.interpretation_defaults.items():
            pv = params.get(k, dv)
            if pv != dv:
                invalid.append({"key": k, "expected": dv, "actual": pv})
        return {
            "data_matches_baseline": data_matches_baseline,
            "interpretation_valid": len(invalid) == 0,
            "invalid_reasons": invalid,
        }


def register(cls):
    _REGISTRY[cls.name] = cls()
    return cls


def get(name):
    return _REGISTRY.get(name)


def all_boards():
    return list(_REGISTRY.values())


def board_meta(tool):
    """供 /api/boards 返回的单个板块元信息。"""
    return {
        "name": tool.name,
        "label": tool.label,
        "description": tool.description,
        "params": [p.to_dict() for p in tool.params],
        "interpretation_defaults": tool.interpretation_defaults,
        "interpretation": tool.interpretation,
    }


def run_board(name, raw_params, data_matches_baseline):
    """统一入口：校验参数 → 计算 → 包装结果与有效性。"""
    tool = get(name)
    if tool is None:
        return None
    params = coerce_params(tool.params, raw_params or {})
    result = tool.run(params)
    result["board"] = name
    result["params"] = params
    result["interpretation"] = tool.interpretation
    result["validity"] = tool.validity(params, data_matches_baseline)
    return result
