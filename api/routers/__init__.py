"""API 路由模块集合（按资源拆分为 meta / boards / jobs）。

各模块导出 ``router: APIRouter``，由 :func:`api.app.create_app` 统一 ``include_router``。
统一前缀 ``/api``，便于将来拆分 OpenAPI tags 与版本化。
"""
