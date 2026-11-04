"""在容器内执行的同步助手；仅依赖 Python 标准库，不递归导出目录。"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tarfile
from pathlib import Path, PurePosixPath


def safe_path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if not parts or parts[0] not in {"work", "skills"} or any(p in {"..", "."} for p in parts):
        raise ValueError(f"Invalid sync path: {relative}")
    target = root.joinpath(*parts)
    for parent in target.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError(f"Symlink parent: {relative}")
    return target


def describe(path: Path) -> dict:
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        return {"type": "symlink", "target": os.readlink(path), "mode": mode}
    if stat.S_ISDIR(info.st_mode):
        return {"type": "directory", "mode": mode}
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Unsupported file type: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.lstat()
    if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise RuntimeError(f"File changed during sync: {path}")
    return {"type": "file", "size": info.st_size, "sha256": digest.hexdigest(), "mode": mode}


def main() -> None:
    request = json.load(sys.stdin)
    root = Path(request.get("root", "/workspace"))
    if request["action"] == "inspect":
        result = {}
        for relative in request["paths"]:
            path = safe_path(root, relative)
            try:
                result[relative] = describe(path)
            except FileNotFoundError:
                result[relative] = None
        json.dump(result, sys.stdout)
        return
    if request["action"] != "export":
        raise ValueError("Unknown sync action")
    # recursive=False 是协议约束；即使路径变成目录，也绝不能把镜像下层导出。
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
        for relative, expected in request["entries"].items():
            path = safe_path(root, relative)
            if describe(path) != expected:
                raise RuntimeError(f"File changed before export: {relative}")
            archive.add(path, arcname=relative, recursive=False)
            if describe(path) != expected:
                raise RuntimeError(f"File changed during export: {relative}")


if __name__ == "__main__":
    main()
