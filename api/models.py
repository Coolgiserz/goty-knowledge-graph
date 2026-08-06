"""参数 schema 与工具结果的基础数据结构。

每个探索板块声明一组 ParamSpec（前端据此自动渲染参数控件），
并声明「解读默认值」interpretation_defaults——当用户调节参数偏离这些默认值时，
该板块的预写解读即视为失效（前端据此把解读框置灰/划线）。
"""


class ParamSpec:
    """单个可调参数的声明（前端据此渲染控件，后端据此校验/转换）。"""

    def __init__(
        self,
        key,
        label,
        type,
        default,
        *,
        options=None,
        min=None,
        max=None,
        step=None,
        help="",
        group="",
    ):
        self.key = key
        self.label = label
        self.type = type  # select | int | float | bool
        self.default = default
        self.options = options  # select 类型的可选项
        self.min = min
        self.max = max
        self.step = step
        self.help = help
        self.group = group

    def to_dict(self):
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "default": self.default,
            "options": self.options,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "help": self.help,
            "group": self.group,
        }

    def coerce(self, v):
        """把前端/请求传来的值按类型安全转换；非法则回退默认值。"""
        if v is None:
            return self.default
        try:
            if self.type == "int":
                return int(v)
            if self.type == "float":
                return float(v)
            if self.type == "bool":
                if isinstance(v, bool):
                    return v
                return str(v).strip().lower() in ("1", "true", "yes", "on")
            if self.type == "select":
                return v if (self.options is None or v in self.options) else self.default
            return v
        except Exception:
            return self.default


def coerce_params(specs, raw):
    """用参数 schema 把原始请求参数转换为合法值字典。"""
    out = {}
    for spec in specs:
        out[spec.key] = spec.coerce(raw.get(spec.key, spec.default))
    return out
