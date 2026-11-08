"""私有文件存储与分片上传。

设计要点（对应 `docs/模块边界与验收细则.md` §4）：
- 文件名由服务端生成，绝不把用户文件名当路径；
- 分片按序号落盘，重复提交同一分片不追加字节；
- 合并后计算 SHA-256 与长度，只有在图像真实解码通过后才创建影像记录；
- 业务记录（数据库）与文件（磁盘）不假定同一事务，失败留下的孤立文件可清理。
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from . import config

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class UploadStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or config.CHUNK_DIR)

    def chunk_dir(self, upload_id: str) -> Path:
        return self.root / upload_id

    def chunk_path(self, upload_id: str, index: int) -> Path:
        return self.chunk_dir(upload_id) / f"{index:06d}.part"

    def write_chunk(self, upload_id: str, index: int, data: bytes) -> None:
        directory = self.chunk_dir(upload_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = self.chunk_path(upload_id, index)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(target)

    def received(self, upload_id: str) -> int:
        directory = self.chunk_dir(upload_id)
        if not directory.is_dir():
            return 0
        return sum(1 for _ in directory.glob("*.part"))

    def merge(self, upload_id: str, total: int | None, suffix: str) -> tuple[Path, int, str]:
        """按序号合并分片，写到 blob 目录，返回 (路径, 字节数, sha256)。"""
        directory = self.chunk_dir(upload_id)
        parts = sorted(directory.glob("*.part"))
        if not parts:
            raise FileNotFoundError("没有已接收的分片")
        if total is not None and len(parts) != total:
            raise ValueError(f"分片不完整：{len(parts)}/{total}")

        config.BLOB_DIR.mkdir(parents=True, exist_ok=True)
        target = config.BLOB_DIR / f"{upload_id}{suffix}"
        digest = hashlib.sha256()
        size = 0
        with target.open("wb") as out:
            for part in parts:
                payload = part.read_bytes()
                out.write(payload)
                digest.update(payload)
                size += len(payload)
        return target, size, digest.hexdigest()

    def discard(self, upload_id: str) -> None:
        directory = self.chunk_dir(upload_id)
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)


def blob_path(stored_name: str) -> Path:
    return config.BLOB_DIR / stored_name


def preview_path(image_id: str) -> Path:
    return config.PREVIEW_DIR / f"{image_id}.jpg"


def result_path(task_id: str, attempt: int) -> Path:
    return config.RESULT_DIR / f"{task_id}_a{attempt}.json"


def export_dir(export_id: str) -> Path:
    return config.EXPORT_DIR / export_id


def sha256_file(path: Path) -> str:
    return _sha256_file(Path(path))
