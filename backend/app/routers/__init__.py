"""业务路由汇总：区间复核等专用路由在前，通用模块资源路由兜底。"""
from __future__ import annotations

from app.routers import interval_review, resource

# 区间复核使用固定前缀 /api/interval-review，必须排在通用 /api/{module}
# 资源路由之前注册，否则固定路径会被动态路径抢先匹配。
ROUTERS = [interval_review, resource]
