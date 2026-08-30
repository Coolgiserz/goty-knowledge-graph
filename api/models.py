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

    def _clamp(self, v):
        """把数值裁剪到 [min, max]；未声明边界则原样返回。

        必须做裁剪而非只做类型检查：前端控件的 min/max 只是提示，请求可由客户端
        任意构造。此前 min/max 声明了却从不生效，导致诸如 ``num_walks``（声明上限 60）
        被传 6000 时单次计算耗时从 1s 涨到 60s 以上、更大值直接吃爆内存——
        任何登录用户一个请求即可打满工作线程，构成 DoS。
        """
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return v
        if self.min is not None and v < self.min:
            return self.min
        if self.max is not None and v > self.max:
            return self.max
        return v

    def coerce(self, v):
        """把前端/请求传来的值按类型安全转换并裁剪到 [min, max]；非法则回退默认值。"""
        if v is None:
            return self.default
        try:
            if self.type == "int":
                return int(self._clamp(int(v)))
            if self.type == "float":
                return float(self._clamp(float(v)))
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
