"""
Pydantic数据模型定义
"""
from typing import Optional, List, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field


class LanguageCode(str, Enum):
    """支持的语言代码"""
    ENGLISH = "en"
    FRENCH = "fr"
    SPANISH = "es"
    GERMAN = "de"
    CHINESE_SIMPLIFIED = "zh-CN"
    CHINESE_TRADITIONAL = "zh-TW"
    JAPANESE = "ja"


class LLMProvider(str, Enum):
    """LLM提供者"""
    OPENAI = "openai"
    CLAUDE_CLI = "claude_cli"
    CODEX_CLI = "codex_cli"


class TranslationStatus(str, Enum):
    """翻译状态"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# ============ 请求模型 ============

class TranslationRequest(BaseModel):
    """翻译请求"""
    file_id: str = Field(..., description="上传文件的ID")
    source_lang: LanguageCode = Field(default=LanguageCode.ENGLISH, description="源语言")
    target_lang: LanguageCode = Field(default=LanguageCode.CHINESE_SIMPLIFIED, description="目标语言")
    llm_provider: LLMProvider = Field(default=LLMProvider.OPENAI, description="LLM提供者")
    api_key: Optional[str] = Field(default=None, description="API密钥（仅OpenAI需要）")
    api_base: Optional[str] = Field(default=None, description="API基础URL（可选）")
    model: Optional[str] = Field(default=None, description="模型名称（可选）")
    skip_references: bool = Field(default=True, description="是否跳过参考文献部分")
    skip_appendix: bool = Field(default=True, description="是否跳过附录部分")
    original_filename: Optional[str] = Field(default=None, description="原始文件名")


class LLMConfigRequest(BaseModel):
    """LLM配置请求"""
    provider: LLMProvider
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    model: Optional[str] = None


# ============ 响应模型 ============

class FileUploadResponse(BaseModel):
    """文件上传响应"""
    file_id: str
    filename: str
    original_filename: str
    size: int
    page_count: int
    message: str = "文件上传成功"


class TranslationProgress(BaseModel):
    """翻译进度"""
    task_id: str
    status: TranslationStatus
    progress: float = Field(ge=0, le=100, description="进度百分比")
    current_page: int = 0
    total_pages: int = 0
    message: str = ""


class TranslationResult(BaseModel):
    """翻译结果"""
    task_id: str
    status: TranslationStatus
    output_file_id: Optional[str] = None
    download_url: Optional[str] = None
    message: str = ""


class LanguageInfo(BaseModel):
    """语言信息"""
    code: str
    name: str


class SupportedLanguagesResponse(BaseModel):
    """支持的语言列表响应"""
    languages: List[LanguageInfo]


class ErrorResponse(BaseModel):
    """错误响应"""
    error: str
    detail: Optional[str] = None


# ============ 内部数据模型 ============

class TextBlock(BaseModel):
    """文本块"""
    text: str
    bbox: tuple  # (x0, y0, x1, y1)
    page_num: int
    block_type: str = "text"  # text, header, footer, caption
    font_size: Optional[float] = None
    font_name: Optional[str] = None
    is_continued: bool = False  # 是否是跨页段落的一部分


class ImageBlock(BaseModel):
    """图片块"""
    image_data: bytes
    bbox: tuple
    page_num: int
    text_regions: List[Dict[str, Any]] = []  # 图片中的文字区域


class PageContent(BaseModel):
    """页面内容"""
    page_num: int
    text_blocks: List[TextBlock] = []
    image_blocks: List[ImageBlock] = []
    width: float
    height: float


class DocumentContent(BaseModel):
    """文档内容"""
    pages: List[PageContent]
    total_pages: int
    metadata: Dict[str, Any] = {}


class AbbreviationEntry(BaseModel):
    """缩写条目"""
    abbreviation: str
    full_form: str
    translation: str
    first_occurrence_page: int


# ============ 用户相关模型（预留接口） ============

class UserBase(BaseModel):
    """用户基础模型"""
    username: str
    email: Optional[str] = None


class UserCreate(UserBase):
    """用户创建模型"""
    password: str


class User(UserBase):
    """用户模型"""
    id: int
    is_active: bool = True
    
    class Config:
        from_attributes = True


class TranslationHistory(BaseModel):
    """翻译历史记录（预留）"""
    id: int
    user_id: Optional[int] = None
    original_filename: str
    source_lang: str
    target_lang: str
    status: TranslationStatus
    created_at: str
    completed_at: Optional[str] = None
    output_file_id: Optional[str] = None
