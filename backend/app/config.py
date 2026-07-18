"""
应用配置管理
"""
import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用设置"""
    
    # 应用基本配置
    APP_NAME: str = "DocumentTranslationAssistant"
    DEBUG: bool = True
    
    # 文件存储路径
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    UPLOAD_DIR: Path = BASE_DIR / "uploads"
    OUTPUT_DIR: Path = BASE_DIR / "outputs"
    TEMP_DIR: Path = BASE_DIR / "temp"
    
    # LLM配置
    LLM_PROVIDER: str = "openai"  # openai, claude_cli, codex_cli
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_API_BASE: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4"
    
    # CLI路径配置
    CLAUDE_CLI_PATH: Optional[str] = None
    CODEX_CLI_PATH: Optional[str] = None
    
    # 翻译配置
    DEFAULT_SOURCE_LANG: str = "en"
    DEFAULT_TARGET_LANG: str = "zh-CN"
    
    # 支持的语言
    SUPPORTED_LANGUAGES: dict = {
        "en": "English",
        "fr": "Français",
        "es": "Español",
        "de": "Deutsch",
        "zh-CN": "简体中文",
        "zh-TW": "正體中文",
        "ja": "日本語"
    }
    
    # PDF处理配置
    MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50MB
    ALLOWED_EXTENSIONS: set = {".pdf"}
    CJK_FONT_PATH: Optional[Path] = None  # 可选：中文/日文字体（TTF/TTC/OTF）
    OCR_ENABLED: bool = True
    TESSERACT_CMD: Optional[Path] = None  # 可选：Tesseract 可执行文件路径
    
    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # 忽略不识别的环境变量


settings = Settings()

# 确保必要的目录存在
for dir_path in [settings.UPLOAD_DIR, settings.OUTPUT_DIR, settings.TEMP_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)
