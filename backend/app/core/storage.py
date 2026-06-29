"""本地卷对象存储（v1）。

把素材字节写入 `settings.storage_local_dir`（compose 卷 objects:/data/objects）。
预留 MinIO/S3 接口：后续可替换 save_bytes/open_stream 的实现，调用方不变。
"""

from pathlib import Path

from app.config import settings


def _root() -> Path:
    root = Path(settings.storage_local_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_bytes(relative_path: str, data: bytes) -> tuple[str, int]:
    """保存字节到卷内相对路径，返回 (相对路径, 字节数)。"""
    target = _root() / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return relative_path, len(data)


def resolve(relative_path: str) -> Path:
    """把卷内相对路径解析为绝对路径（用于读取/播放）。"""
    return _root() / relative_path


def exists(relative_path: str) -> bool:
    return resolve(relative_path).exists()
