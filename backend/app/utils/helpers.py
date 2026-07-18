"""
工具函数
"""
import uuid
import hashlib
import json
from pathlib import Path
from typing import Optional, Dict, Any
import aiofiles
from fastapi import UploadFile

from app.config import settings


def generate_file_id() -> str:
    """生成文件ID"""
    return str(uuid.uuid4())


def get_file_hash(content: bytes) -> str:
    """计算文件哈希"""
    return hashlib.md5(content).hexdigest()


def get_file_extension(filename: str) -> str:
    """获取文件扩展名"""
    return Path(filename).suffix.lower()


def is_allowed_file(filename: str) -> bool:
    """检查文件是否允许上传"""
    ext = get_file_extension(filename)
    return ext in settings.ALLOWED_EXTENSIONS


async def save_upload_file(
    upload_file: UploadFile,
    file_id: str
) -> Path:
    """
    保存上传的文件
    
    Args:
        upload_file: 上传的文件
        file_id: 文件ID
    
    Returns:
        保存的文件路径
    """
    ext = get_file_extension(upload_file.filename or "file.pdf")
    save_path = settings.UPLOAD_DIR / f"{file_id}{ext}"
    
    async with aiofiles.open(save_path, 'wb') as f:
        content = await upload_file.read()
        await f.write(content)
    
    return save_path


def get_upload_path(file_id: str, ext: str = ".pdf") -> Path:
    """获取上传文件路径"""
    return settings.UPLOAD_DIR / f"{file_id}{ext}"


def get_output_path(file_id: str) -> Path:
    """获取输出文件路径"""
    return settings.OUTPUT_DIR / f"translated_{file_id}.pdf"


def cleanup_temp_files(file_id: str):
    """清理临时文件"""
    # 清理上传文件
    upload_path = get_upload_path(file_id)
    if upload_path.exists():
        upload_path.unlink()
    
    # 清理临时文件
    for temp_file in settings.TEMP_DIR.glob(f"{file_id}*"):
        temp_file.unlink()


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"


def validate_language_code(lang_code: str) -> bool:
    """验证语言代码"""
    return lang_code in settings.SUPPORTED_LANGUAGES


def get_metadata_path(file_id: str) -> Path:
    """获取元数据文件路径"""
    return settings.TEMP_DIR / f"{file_id}_metadata.json"


def save_file_metadata(file_id: str, metadata: Dict[str, Any]) -> None:
    """
    保存文件元数据
    
    Args:
        file_id: 文件ID
        metadata: 元数据字典
    """
    metadata_path = get_metadata_path(file_id)
    
    # 如果已存在元数据，合并新数据
    existing = get_file_metadata(file_id) or {}
    existing.update(metadata)
    
    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def get_file_metadata(file_id: str) -> Optional[Dict[str, Any]]:
    """
    获取文件元数据
    
    Args:
        file_id: 文件ID
    
    Returns:
        元数据字典，如果不存在则返回None
    """
    metadata_path = get_metadata_path(file_id)
    
    if not metadata_path.exists():
        return None
    
    try:
        with open(metadata_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None
