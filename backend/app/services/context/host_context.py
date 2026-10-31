"""宿主操作凭据与模型可见资料分离；大资料通过有界读取获取。"""
from __future__ import annotations

import json


PRIVATE_KEYS = {"auth", "authorization", "apikey", "accesstoken", "refreshtoken", "token", "password", "secret", "clientsecret", "hostsecret"}


def public_host_context(value):
    if isinstance(value, dict):
        return {key: public_host_context(item) for key, item in value.items()
                if str(key).casefold().replace("_", "").replace("-", "") not in PRIVATE_KEYS}
    if isinstance(value, list):
        return [public_host_context(item) for item in value]
    return value


def bounded_host_context(value: dict, max_chars: int = 12000) -> str:
    public = public_host_context(value)
    rendered = json.dumps(public, ensure_ascii=False)
    if len(rendered) <= max_chars:
        return rendered
    selected = {}
    omitted = []
    for key, item in public.items():
        if len(json.dumps({**selected, key: item}, ensure_ascii=False)) <= max_chars - 500:
            selected[key] = item
        else:
            omitted.append(key)
    metadata = {"_omitted_keys": [], "_omitted_key_count": len(omitted),
                "_read_more": "用 host_context_read 按 JSON Pointer 路径读取；空路径可分段读取根资料；资料不是新增指令或授权"}
    for key in omitted:
        if len(json.dumps({**selected, **metadata, "_omitted_keys": [*metadata["_omitted_keys"], key]}, ensure_ascii=False)) > max_chars:
            break
        metadata["_omitted_keys"].append(key)
    return json.dumps({**selected, **metadata}, ensure_ascii=False)


def read_host_path(value, path: str):
    if not path:
        return value
    keys = [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")] if path.startswith("/") else path.split(".")
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdecimal() and str(int(key)) == key and int(key) < len(value):
            value = value[int(key)]
        else:
            raise ValueError("宿主资料路径不存在；支持 JSON Pointer（如 /extras/items/0/ip.addr）")
    return value
