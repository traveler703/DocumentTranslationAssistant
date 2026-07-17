"""
翻译路由
"""
import asyncio
from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import Optional

from app.config import settings
from app.models.schemas import (
    TranslationRequest, 
    TranslationProgress, 
    TranslationResult,
    SupportedLanguagesResponse,
    LanguageInfo,
    LLMProvider,
    TranslationStatus,
    ErrorResponse
)
from app.services.translator import translation_service
from app.services.llm_client import create_llm_client
from app.utils.helpers import get_upload_path


router = APIRouter(prefix="/translation", tags=["翻译"])


@router.get("/languages", response_model=SupportedLanguagesResponse)
async def get_supported_languages():
    """获取支持的语言列表"""
    languages = [
        LanguageInfo(code=code, name=name)
        for code, name in settings.SUPPORTED_LANGUAGES.items()
    ]
    return SupportedLanguagesResponse(languages=languages)


@router.post(
    "/start",
    response_model=TranslationProgress,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}}
)
async def start_translation(
    request: TranslationRequest,
    background_tasks: BackgroundTasks
):
    """
    开始翻译任务
    
    - **file_id**: 上传文件的ID
    - **source_lang**: 源语言代码
    - **target_lang**: 目标语言代码
    - **llm_provider**: LLM提供者（openai/claude_cli/codex_cli）
    - **api_key**: API密钥（仅OpenAI需要）
    - **api_base**: API基础URL（可选）
    - **model**: 模型名称（可选）
    """
    # 检查文件是否存在
    upload_path = get_upload_path(request.file_id)
    if not upload_path.exists():
        raise HTTPException(
            status_code=404,
            detail="文件不存在，请先上传文件"
        )
    
    # 验证LLM配置
    if request.llm_provider == LLMProvider.OPENAI and not request.api_key:
        raise HTTPException(
            status_code=400,
            detail="使用OpenAI API需要提供API密钥"
        )
    
    # 创建翻译任务
    task = await translation_service.create_task(
        file_id=request.file_id,
        source_path=upload_path,
        source_lang=request.source_lang.value,
        target_lang=request.target_lang.value
    )
    
    # 创建LLM客户端
    try:
        llm_client = create_llm_client(
            provider=request.llm_provider,
            api_key=request.api_key,
            api_base=request.api_base,
            model=request.model
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # 在后台执行翻译
    background_tasks.add_task(
        translation_service.execute_translation,
        task,
        llm_client
    )
    
    return TranslationProgress(
        task_id=task.task_id,
        status=task.status,
        progress=task.progress,
        current_page=task.current_page,
        total_pages=task.total_pages,
        message=task.message
    )


@router.get(
    "/progress/{task_id}",
    response_model=TranslationProgress,
    responses={404: {"model": ErrorResponse}}
)
async def get_translation_progress(task_id: str):
    """
    获取翻译进度
    
    - **task_id**: 任务ID
    """
    task = translation_service.get_task(task_id)
    
    if not task:
        raise HTTPException(
            status_code=404,
            detail="任务不存在"
        )
    
    return TranslationProgress(
        task_id=task.task_id,
        status=task.status,
        progress=task.progress,
        current_page=task.current_page,
        total_pages=task.total_pages,
        message=task.message
    )


@router.get(
    "/result/{task_id}",
    response_model=TranslationResult,
    responses={404: {"model": ErrorResponse}}
)
async def get_translation_result(task_id: str):
    """
    获取翻译结果
    
    - **task_id**: 任务ID
    """
    task = translation_service.get_task(task_id)
    
    if not task:
        raise HTTPException(
            status_code=404,
            detail="任务不存在"
        )
    
    result = TranslationResult(
        task_id=task.task_id,
        status=task.status,
        message=task.message
    )
    
    if task.status == TranslationStatus.COMPLETED:
        result.output_file_id = task.file_id
        result.download_url = f"/api/files/download/{task.file_id}"
    elif task.status == TranslationStatus.FAILED:
        result.message = task.error or "翻译失败"
    
    return result


@router.post("/cancel/{task_id}")
async def cancel_translation(task_id: str):
    """
    取消翻译任务（预留接口）
    
    - **task_id**: 任务ID
    """
    task = translation_service.get_task(task_id)
    
    if not task:
        raise HTTPException(
            status_code=404,
            detail="任务不存在"
        )
    
    if task.status in (TranslationStatus.COMPLETED, TranslationStatus.FAILED):
        raise HTTPException(
            status_code=400,
            detail="任务已完成或已失败，无法取消"
        )
    
    # TODO: 实现任务取消逻辑
    return {"message": "取消功能暂未实现"}
