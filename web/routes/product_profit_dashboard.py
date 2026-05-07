"""产品盈亏可视化看板（独立侧栏菜单 /product-profit）。

页面路由独立挂在 / 根路径，但数据接口复用 /order-analytics/product-profit/* 的
现成 endpoint（products / report.json / report.xlsx）。
"""
from __future__ import annotations

import logging

from flask import Blueprint, render_template
from flask_login import login_required

from web.auth import permission_required

log = logging.getLogger(__name__)

bp = Blueprint("product_profit_dashboard", __name__)


@bp.route("/product-profit")
@login_required
@permission_required("product_profit")
def page():
    """产品盈亏可视化看板入口。"""
    return render_template("product_profit_dashboard.html")
