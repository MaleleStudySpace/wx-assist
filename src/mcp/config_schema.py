"""MCP server 配置校验。

校验 data/user_mcp.json 中每个 server 配置项。
每项支持两种 transport: stdio (本地进程) / http (远程 HTTP Streamable HTTP)。
"""

# 配置字段定义
STDIO_FIELDS = {
    "name": {"type": str, "required": True, "min_len": 2, "max_len": 32},
    "transport": {"type": str, "required": True, "enum": ["stdio"]},
    "command": {"type": str, "required": True},
    "args": {"type": list, "required": False},
    "cwd": {"type": str, "required": False},
    "env": {"type": dict, "required": False},
    "timeout": {"type": (int, float), "required": False},
    "auto_restart": {"type": bool, "required": False},
    "enabled": {"type": bool, "required": False},
    "description": {"type": str, "required": False},
}

HTTP_FIELDS = {
    "name": {"type": str, "required": True, "min_len": 2, "max_len": 32},
    "transport": {"type": str, "required": True, "enum": ["http"]},
    "url": {"type": str, "required": True},
    "headers": {"type": dict, "required": False},
    "timeout": {"type": (int, float), "required": False},
    "auto_restart": {"type": bool, "required": False},
    "enabled": {"type": bool, "required": False},
    "description": {"type": str, "required": False},
}


def _check_type(value, expected):
    """类型检查，tuple=允许多种类型。"""
    if isinstance(expected, tuple):
        return isinstance(value, expected)
    return isinstance(value, expected)


def _validate_item(item, fields):
    """校验单个配置项。返回 (ok, errors)。"""
    errors = []
    for field, rules in fields.items():
        is_required = rules.get("required", False)
        value = item.get(field)

        if is_required and value is None:
            errors.append("{}: 缺少必填字段".format(field))
            continue

        if value is not None:
            if "enum" in rules and value not in rules["enum"]:
                errors.append("{}: 值必须是 {} 之一 (got {})".format(field, rules["enum"], value))
            if "min_len" in rules and isinstance(value, str) and len(value) < rules["min_len"]:
                errors.append("{}: 至少 {} 个字符 (got {})".format(field, rules["min_len"], len(value)))
            if "max_len" in rules and isinstance(value, str) and len(value) > rules["max_len"]:
                errors.append("{}: 最多 {} 个字符 (got {})".format(field, rules["max_len"], len(value)))
            if not _check_type(value, rules["type"]):
                errors.append("{}: 类型应为 {} (got {})".format(field, rules["type"], type(value).__name__))

    return len(errors) == 0, errors


def validate_config(configs):
    """校验整个 user_mcp.json 配置列表。

    输入: configs = list[dict]   (从 data/user_mcp.json 读取)
    返回: {"ok": bool, "errors": dict[str, list[str]], "valid_items": list[dict]}
    """
    if not isinstance(configs, (list, tuple)):
        return {"ok": False, "errors": {"_root": ["配置必须是列表"]}, "valid_items": []}

    seen_names = set()
    all_errors = {}
    valid_items = []

    for i, item in enumerate(configs):
        name = item.get("name", "<index {}>".format(i))
        transport = item.get("transport", "")

        # 检查名称唯一性
        if name in seen_names:
            all_errors[name] = ["名称重复"]
            continue
        seen_names.add(name)

        # 字段校验
        if transport == "stdio":
            ok, errs = _validate_item(item, STDIO_FIELDS)
        elif transport == "http":
            ok, errs = _validate_item(item, HTTP_FIELDS)
        else:
            ok, errs = False, ["transport 必须是 'stdio' 或 'http' (got '{}')".format(transport)]

        if ok:
            valid_items.append(item)
        else:
            all_errors[name] = errs

    return {
        "ok": len(all_errors) == 0,
        "errors": all_errors,
        "valid_items": valid_items,
    }
