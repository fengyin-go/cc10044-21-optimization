"""业务路由汇总：领域专用接口先于通用资源路由注册，避免被动态路径抢先匹配。"""
from __future__ import annotations

from app.routers import resource, restore_flow

ROUTERS = [restore_flow, resource]
