"""
文件上传路由
"""
import json
from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from fastapi.responses import FileResponse

from app.config import settings
from app.models.schemas import FileUploadResponse, ErrorResponse
from app.utils.helpers import (
    generate_file_id, 
    save_upload_file, 
    is_allowed_file,
    get_upload_path,
    get_output_path,
    save_file_metadata,
    get_file_metadata
)
from app.services.pdf_processor import PDFProcessor


router = APIRouter(prefix="/files", tags=["文件管理"])


@router.post(
    "/upload",
    response_model=FileUploadResponse,
    responses={400: {"model": ErrorResponse}}
)
async def upload_file(file: UploadFile = File(...)):
    """
    上传PDF文件
    
    - **file**: PDF文件
    """
    # 验证文件类型
    if not file.filename or not is_allowed_file(file.filename):
        raise HTTPException(
            status_code=400,
            detail="只支持PDF文件格式"
        )
    
    # 验证文件大小
    content = await file.read()
    if len(content) > settings.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"文件大小超过限制（最大{settings.MAX_FILE_SIZE // 1024 // 1024}MB）"
        )
    
    # 重置文件指针
    await file.seek(0)
    
    # 生成文件ID并保存
    file_id = generate_file_id()
    save_path = await save_upload_file(file, file_id)
    
    # 保存原始文件名元数据
    original_filename = file.filename or "document.pdf"
    save_file_metadata(file_id, {"original_filename": original_filename})
    
    # 获取PDF页数
    try:
        with PDFProcessor(str(save_path)) as processor:
            page_count = processor.page_count
    except Exception as e:
        # 删除无效文件
        save_path.unlink()
        raise HTTPException(
            status_code=400,
            detail=f"无法解析PDF文件: {str(e)}"
        )
    
    return FileUploadResponse(
        file_id=file_id,
        filename=file.filename,
        original_filename=original_filename,
        size=len(content),
        page_count=page_count
    )


@router.get("/download/{file_id}")
async def download_file(file_id: str):
    """
    下载翻译后的PDF文件
    
    - **file_id**: 文件ID
    """
    output_path = get_output_path(file_id)
    
    if not output_path.exists():
        raise HTTPException(
            status_code=404,
            detail="文件不存在或翻译尚未完成"
        )
    
    # 获取元数据以构建正确的文件名
    metadata = get_file_metadata(file_id)
    original_filename = metadata.get("original_filename", "document.pdf") if metadata else "document.pdf"
    target_lang = metadata.get("target_lang", "zh-CN") if metadata else "zh-CN"
    
    # 语言代码映射
    lang_code_map = {
        "en": "EN",
        "fr": "FR",
        "es": "ES",
        "de": "DE",
        "zh-CN": "CN",
        "zh-TW": "TW",
        "ja": "JP"
    }
    lang_suffix = lang_code_map.get(target_lang, "CN")
    
    # 去掉原文件名的扩展名，构建新文件名
    import os
    base_name = os.path.splitext(original_filename)[0]
    download_filename = f"{base_name}_translated_{lang_suffix}.pdf"
    
    return FileResponse(
        path=str(output_path),
        filename=download_filename,
        media_type="application/pdf"
    )


@router.delete("/{file_id}")
async def delete_file(file_id: str):
    """
    删除文件
    
    - **file_id**: 文件ID
    """
    from app.utils.helpers import cleanup_temp_files
    
    upload_path = get_upload_path(file_id)
    output_path = get_output_path(file_id)
    
    deleted = False
    
    if upload_path.exists():
        upload_path.unlink()
        deleted = True
    
    if output_path.exists():
        output_path.unlink()
        deleted = True
    
    cleanup_temp_files(file_id)
    
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="文件不存在"
        )
    
    return {"message": "文件已删除"}
