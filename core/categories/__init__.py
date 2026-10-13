"""分类体检包.

导入本包即完成所有分类的注册 (顺序无关, 靠 base.register 装饰器).
新增分类只需在此处 import 一行。
"""

from .base import (REGISTRY, Category, CategoryResult, Service,  # noqa: F401
                   all_categories, build, register)

# 注册内置分类
from . import google  # noqa: F401,E402
from . import xai     # noqa: F401,E402

__all__ = ["REGISTRY", "Category", "CategoryResult", "Service",
           "all_categories", "build", "register"]
