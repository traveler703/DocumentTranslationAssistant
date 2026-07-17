"""
FastAPI 应用入口
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers import upload, translation, user


# 创建FastAPI应用
app = FastAPI(
    title=settings.APP_NAME,
    description="PDF文档翻译助手 - 支持多语言翻译，保留原文排版",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json"
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发环境允许所有来源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(upload.router, prefix="/api")
app.include_router(translation.router, prefix="/api")
app.include_router(user.router, prefix="/api")


@app.get("/")
async def root():
    """根路径"""
    return {
        "name": settings.APP_NAME,
        "version": "1.0.0",
        "docs": "/api/docs"
    }


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy"}


@app.get("/api/config/languages")
async def get_language_config():
    """获取语言配置"""
    return {
        "supported_languages": settings.SUPPORTED_LANGUAGES,
        "default_source": settings.DEFAULT_SOURCE_LANG,
        "default_target": settings.DEFAULT_TARGET_LANG
    }


@app.get("/api/config/llm-providers")
async def get_llm_providers():
    """获取LLM提供者配置"""
    return {
        "providers": [
            {
                "id": "openai",
                "name": "OpenAI API",
                "description": "使用OpenAI或兼容API（需要API Key）",
                "requires_api_key": True
            },
            {
                "id": "claude_cli",
                "name": "Claude CLI",
                "description": "使用本地Claude CLI",
                "requires_api_key": False
            },
            {
                "id": "codex_cli",
                "name": "Codex CLI",
                "description": "使用本地Codex CLI",
                "requires_api_key": False
            }
        ]
    }
