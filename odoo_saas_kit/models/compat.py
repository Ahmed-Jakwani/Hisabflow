# -*- coding: utf-8 -*-

import os
from pathlib import Path

try:
    from odoo.modules.module import get_module_resource as _odoo_get_module_resource
except ImportError:
    _odoo_get_module_resource = None

try:
    from odoo.modules.module import get_module_path as _odoo_get_module_path
except ImportError:
    _odoo_get_module_path = None

try:
    from odoo.models import NewId as _NewId
except ImportError:
    try:
        from odoo.orm.models import NewId as _NewId
    except ImportError:
        _NewId = None


def get_module_resource(module, *path):
    if _odoo_get_module_resource:
        return _odoo_get_module_resource(module, *path)

    if _odoo_get_module_path:
        module_path = _odoo_get_module_path(module)
    elif module == "odoo_saas_kit":
        module_path = Path(__file__).resolve().parents[1]
    else:
        module_path = False

    if not module_path:
        return False

    return os.path.join(str(module_path), *path)


def is_new_id(value):
    if _NewId:
        return isinstance(value, _NewId)
    return type(value).__name__ == "NewId"
