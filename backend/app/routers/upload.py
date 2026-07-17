"""
文件上传路由
"""
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse

from app.config import settings
from app.models.schemas import FileUploadResponse, ErrorResponse
from app.utils.helpers import (
    generate_file_id, 
    save_upload_file, 
    is_allowed_file,
    get_upload_path,
    get_output_path
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
    
    return FileResponse(
        path=str(output_path),
        filename=f"translated_{file_id}.pdf",
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
