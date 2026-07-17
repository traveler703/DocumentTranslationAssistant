"""
用户路由（预留接口）
"""
from fastapi import APIRouter, HTTPException
from typing import Optional

from app.models.schemas import User, UserCreate, TranslationHistory


router = APIRouter(prefix="/users", tags=["用户管理（预留）"])


@router.post("/register", response_model=User)
async def register_user(user: UserCreate):
    """
    用户注册（预留接口）
    """
    # TODO: 实现用户注册逻辑
    raise HTTPException(
        status_code=501,
        detail="用户注册功能暂未实现"
    )


@router.post("/login")
async def login(username: str, password: str):
    """
    用户登录（预留接口）
    """
    # TODO: 实现用户登录逻辑
    raise HTTPException(
        status_code=501,
        detail="用户登录功能暂未实现"
    )


@router.get("/me", response_model=User)
async def get_current_user():
    """
    获取当前用户信息（预留接口）
    """
    # TODO: 实现获取当前用户逻辑
    raise HTTPException(
        status_code=501,
        detail="用户功能暂未实现"
    )


@router.get("/history")
async def get_translation_history(
    page: int = 1,
    page_size: int = 10
):
    """
    获取翻译历史（预留接口）
    """
    # TODO: 实现翻译历史查询逻辑
    raise HTTPException(
        status_code=501,
        detail="翻译历史功能暂未实现"
    )


@router.delete("/history/{history_id}")
async def delete_translation_history(history_id: int):
    """
    删除翻译历史记录（预留接口）
    """
    # TODO: 实现删除翻译历史逻辑
    raise HTTPException(
        status_code=501,
        detail="翻译历史功能暂未实现"
    )
