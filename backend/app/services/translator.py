"""
翻译服务 - 协调PDF处理、LLM翻译和图片处理
支持并行翻译和token优化
"""
import asyncio
import uuid
import re
from typing import Dict, List, Optional, Tuple, Callable
from pathlib import Path
from dataclasses import dataclass, field

from app.config import settings
from app.models.schemas import (
    LLMProvider, TranslationStatus, LanguageCode
)
from app.services.pdf_processor import PDFProcessor, PDFGenerator, TextBlock, PageLayout
from app.services.image_processor import ImageProcessor, TextRegion
from app.services.llm_client import create_llm_client, BaseLLMClient


# 并行翻译配置
MAX_CONCURRENT_TRANSLATIONS = 5  # 最大并行翻译数
BATCH_SIZE_PAGES = 5  # 每批处理的页数
MAX_TEXT_PER_BATCH = 8000  # 每批最大字符数（减少token使用）


@dataclass
class TranslationTask:
    """翻译任务"""
    task_id: str
    file_id: str
    source_path: Path
    output_path: Optional[Path] = None
    source_lang: str = "en"
    target_lang: str = "zh-CN"
    status: TranslationStatus = TranslationStatus.PENDING
    progress: float = 0.0
    current_page: int = 0
    total_pages: int = 0
    message: str = ""
    error: Optional[str] = None
    abbreviations: Dict[str, str] = field(default_factory=dict)  # 缩写 -> 翻译


@dataclass
class PageBatch:
    """页面批次"""
    page_nums: List[int]
    layouts: List[PageLayout]
    combined_text: str
    block_map: List[Tuple[int, int]]  # [(page_idx, block_idx), ...]


class TranslationService:
    """翻译服务 - 支持并行翻译"""
    
    def __init__(self):
        self.tasks: Dict[str, TranslationTask] = {}
        self.image_processor = ImageProcessor()
    
    async def create_task(
        self,
        file_id: str,
        source_path: Path,
        source_lang: str,
        target_lang: str
    ) -> TranslationTask:
        """创建翻译任务"""
        task_id = str(uuid.uuid4())
        
        # 获取页数
        with PDFProcessor(str(source_path)) as processor:
            total_pages = processor.page_count
        
        output_filename = f"translated_{file_id}.pdf"
        output_path = settings.OUTPUT_DIR / output_filename
        
        task = TranslationTask(
            task_id=task_id,
            file_id=file_id,
            source_path=source_path,
            output_path=output_path,
            source_lang=source_lang,
            target_lang=target_lang,
            total_pages=total_pages
        )
        
        self.tasks[task_id] = task
        return task
    
    def get_task(self, task_id: str) -> Optional[TranslationTask]:
        """获取任务状态"""
        return self.tasks.get(task_id)
    
    async def execute_translation(
        self,
        task: TranslationTask,
        llm_client: BaseLLMClient,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ):
        """执行翻译任务（优化版：并行翻译 + 批量处理）"""
        task.status = TranslationStatus.PROCESSING
        task.message = "正在分析文档结构..."
        
        try:
            # 1. 分析PDF文档
            with PDFProcessor(str(task.source_path)) as processor:
                page_layouts = processor.analyze_document()
                task.total_pages = len(page_layouts)
                images = processor.get_images()
            
            # 2. 设置图片处理器的源语言
            self.image_processor.set_source_language(task.source_lang)
            
            # 3. 预处理：只在第一批文本中检测缩写（节省token）
            task.message = "正在分析文档缩写..."
            await self._detect_abbreviations_once(page_layouts, llm_client, task)
            
            # 4. 创建翻译批次（合并多页，减少API调用）
            batches = self._create_translation_batches(page_layouts)
            task.message = f"已创建 {len(batches)} 个翻译批次，开始并行翻译..."
            
            # 5. 并行翻译文本批次
            page_translations = await self._translate_batches_parallel(
                batches, llm_client, task, progress_callback
            )
            
            # 6. 并行处理图片翻译（如果有）
            task.message = "正在处理图片中的文字..."
            image_replacements = await self._translate_images_parallel(
                images, llm_client, task
            )
            
            # 7. 生成翻译后的PDF
            task.message = "正在生成翻译文档..."
            task.progress = 95
            
            with PDFGenerator(str(task.source_path)) as generator:
                generator.create_translated_pdf(
                    translations=page_translations,
                    output_path=str(task.output_path),
                    image_replacements=image_replacements if image_replacements else None
                )
            
            task.status = TranslationStatus.COMPLETED
            task.progress = 100
            task.message = "翻译完成"
            
        except Exception as e:
            task.status = TranslationStatus.FAILED
            task.error = str(e)
            task.message = f"翻译失败: {str(e)}"
            raise
        
        finally:
            if hasattr(llm_client, 'close'):
                await llm_client.close()
    
    async def _detect_abbreviations_once(
        self,
        page_layouts: List[PageLayout],
        llm_client: BaseLLMClient,
        task: TranslationTask
    ):
        """只检测一次缩写（使用前几页的样本）"""
        # 只用前10页或总页数的20%来检测缩写，节省token
        sample_pages = min(10, max(3, len(page_layouts) // 5))
        
        sample_text_parts = []
        for layout in page_layouts[:sample_pages]:
            for block in layout.text_blocks:
                if block.block_type not in ("header", "footer"):
                    sample_text_parts.append(block.text)
        
        sample_text = "\n".join(sample_text_parts)[:5000]  # 限制长度
        
        if sample_text.strip():
            abbrs = await llm_client.detect_abbreviations(
                sample_text,
                self._get_lang_name(task.target_lang)
            )
            for abbr in abbrs:
                if abbr.get("abbreviation") and abbr.get("translation"):
                    task.abbreviations[abbr["abbreviation"]] = (
                        f"{abbr.get('full_form', '')}，{abbr['translation']}"
                    )
    
    def _create_translation_batches(
        self,
        page_layouts: List[PageLayout]
    ) -> List[PageBatch]:
        """创建翻译批次，合并多页减少API调用"""
        batches = []
        current_batch_pages = []
        current_batch_layouts = []
        current_text_parts = []
        current_block_map = []
        current_text_len = 0
        
        for page_num, layout in enumerate(page_layouts):
            page_text_parts = []
            page_block_indices = []
            
            for block_idx, block in enumerate(layout.text_blocks):
                if block.block_type not in ("header", "footer"):
                    text = block.text.strip()
                    if text:
                        page_text_parts.append(text)
                        page_block_indices.append((len(current_batch_pages), block_idx))
            
            page_text = "\n\n".join(page_text_parts)
            
            # 检查是否需要开始新批次
            if (current_text_len + len(page_text) > MAX_TEXT_PER_BATCH and current_batch_pages) or \
               len(current_batch_pages) >= BATCH_SIZE_PAGES:
                # 保存当前批次
                if current_text_parts:
                    batches.append(PageBatch(
                        page_nums=current_batch_pages.copy(),
                        layouts=current_batch_layouts.copy(),
                        combined_text="\n\n---PAGE_BREAK---\n\n".join(current_text_parts),
                        block_map=current_block_map.copy()
                    ))
                # 重置
                current_batch_pages = []
                current_batch_layouts = []
                current_text_parts = []
                current_block_map = []
                current_text_len = 0
            
            # 添加到当前批次
            if page_text:
                # 更新block_map的page_idx
                for orig_page_idx, block_idx in page_block_indices:
                    current_block_map.append((len(current_batch_pages), block_idx))
                
                current_batch_pages.append(page_num)
                current_batch_layouts.append(layout)
                current_text_parts.append(page_text)
                current_text_len += len(page_text)
        
        # 保存最后一个批次
        if current_text_parts:
            batches.append(PageBatch(
                page_nums=current_batch_pages,
                layouts=current_batch_layouts,
                combined_text="\n\n---PAGE_BREAK---\n\n".join(current_text_parts),
                block_map=current_block_map
            ))
        
        return batches
    
    async def _translate_batches_parallel(
        self,
        batches: List[PageBatch],
        llm_client: BaseLLMClient,
        task: TranslationTask,
        progress_callback: Optional[Callable[[float, str], None]]
    ) -> Dict[int, List[Tuple[Tuple, str, dict]]]:
        """并行翻译多个批次"""
        page_translations: Dict[int, List[Tuple[Tuple, str, dict]]] = {}
        
        # 使用信号量限制并发数
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_TRANSLATIONS)
        completed = [0]  # 使用列表以便在闭包中修改
        
        async def translate_batch(batch: PageBatch) -> Dict[int, List[Tuple[Tuple, str, dict]]]:
            async with semaphore:
                result = await self._translate_single_batch(batch, llm_client, task)
                completed[0] += len(batch.page_nums)
                task.current_page = completed[0]
                task.progress = (completed[0] / task.total_pages) * 90  # 留10%给PDF生成
                task.message = f"已翻译 {completed[0]}/{task.total_pages} 页..."
                if progress_callback:
                    progress_callback(task.progress, task.message)
                return result
        
        # 并行执行所有批次
        results = await asyncio.gather(*[translate_batch(b) for b in batches])
        
        # 合并结果
        for result in results:
            page_translations.update(result)
        
        return page_translations
    
    async def _translate_single_batch(
        self,
        batch: PageBatch,
        llm_client: BaseLLMClient,
        task: TranslationTask
    ) -> Dict[int, List[Tuple[Tuple, str, dict]]]:
        """翻译单个批次"""
        result: Dict[int, List[Tuple[Tuple, str, dict]]] = {}
        
        if not batch.combined_text.strip():
            # 空批次，为每页返回空列表
            for page_num in batch.page_nums:
                result[page_num] = []
            return result
        
        # 翻译合并的文本
        translated_text = await llm_client.translate(
            batch.combined_text,
            self._get_lang_name(task.source_lang),
            self._get_lang_name(task.target_lang),
            abbreviations=task.abbreviations
        )
        
        # 按页分割翻译结果
        translated_pages = translated_text.split("---PAGE_BREAK---")
        
        # 为每页解析翻译结果
        for page_idx, page_num in enumerate(batch.page_nums):
            layout = batch.layouts[page_idx]
            result[page_num] = []
            
            if page_idx < len(translated_pages):
                page_translation = translated_pages[page_idx].strip()
            else:
                page_translation = ""
            
            # 获取该页的文本块
            text_blocks = [
                block for block in layout.text_blocks
                if block.block_type not in ("header", "footer")
            ]
            
            if not text_blocks:
                continue
            
            # 简单策略：如果只有一个块，直接使用整个翻译
            # 如果有多个块，按段落分割
            if len(text_blocks) == 1:
                style = self._extract_text_style(text_blocks[0])
                result[page_num].append((text_blocks[0].bbox, page_translation, style))
            else:
                # 按双换行分割
                translated_parts = [p.strip() for p in page_translation.split("\n\n") if p.strip()]
                
                for i, block in enumerate(text_blocks):
                    if i < len(translated_parts):
                        translated = translated_parts[i]
                    else:
                        # 如果翻译部分不够，合并剩余的
                        translated = "\n\n".join(translated_parts[i:]) if i < len(translated_parts) else block.text
                    
                    style = self._extract_text_style(block)
                    result[page_num].append((block.bbox, translated, style))
        
        return result
    
    async def _translate_images_parallel(
        self,
        images: List,
        llm_client: BaseLLMClient,
        task: TranslationTask
    ) -> Dict[int, List[Tuple[Tuple, bytes]]]:
        """并行处理图片翻译"""
        image_replacements: Dict[int, List[Tuple[Tuple, bytes]]] = {}
        
        if not images:
            return image_replacements
        
        # 按页分组
        images_by_page: Dict[int, List] = {}
        for img in images:
            if img.page_num not in images_by_page:
                images_by_page[img.page_num] = []
            images_by_page[img.page_num].append(img)
        
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_TRANSLATIONS)
        
        async def process_page_images(page_num: int, page_images: List) -> Tuple[int, List[Tuple[Tuple, bytes]]]:
            async with semaphore:
                replacements = []
                for image_info in page_images:
                    if not image_info.image_data:
                        continue
                    
                    # 提取图片中的文字
                    text_regions = self.image_processor.extract_text_from_image(
                        image_info.image_data
                    )
                    
                    if not text_regions:
                        continue
                    
                    # 批量翻译图片中的所有文字（减少API调用）
                    all_texts = [r.text for r in text_regions if r.text.strip()]
                    if not all_texts:
                        continue
                    
                    combined_image_text = "\n---IMG_TEXT_SEP---\n".join(all_texts)
                    translated_combined = await llm_client.translate(
                        combined_image_text,
                        self._get_lang_name(task.source_lang),
                        self._get_lang_name(task.target_lang),
                        abbreviations=task.abbreviations
                    )
                    
                    translated_parts = translated_combined.split("---IMG_TEXT_SEP---")
                    
                    text_replacements = []
                    valid_regions = [r for r in text_regions if r.text.strip()]
                    for i, region in enumerate(valid_regions):
                        if i < len(translated_parts):
                            text_replacements.append((region, translated_parts[i].strip()))
                    
                    if text_replacements:
                        new_image_data = self.image_processor.replace_text_in_image(
                            image_info.image_data,
                            text_replacements,
                            task.target_lang
                        )
                        replacements.append((image_info.bbox, new_image_data))
                
                return page_num, replacements
        
        # 并行处理所有页的图片
        results = await asyncio.gather(*[
            process_page_images(page_num, page_images)
            for page_num, page_images in images_by_page.items()
        ])
        
        for page_num, replacements in results:
            if replacements:
                image_replacements[page_num] = replacements
        
        return image_replacements
    
    def _extract_text_style(self, block: TextBlock) -> dict:
        """提取文本样式"""
        style = {
            "font_size": 11,
            "font_name": "china-s",
            "color": (0, 0, 0)
        }
        
        if block.lines and block.lines[0].spans:
            first_span = block.lines[0].spans[0]
            style["font_size"] = max(8, min(24, first_span.font_size))
            
            # 转换颜色
            color_int = first_span.color
            r = (color_int >> 16) & 0xFF
            g = (color_int >> 8) & 0xFF
            b = color_int & 0xFF
            style["color"] = (r / 255, g / 255, b / 255)
        
        return style
    
    def _get_lang_name(self, lang_code: str) -> str:
        """获取语言名称"""
        lang_names = {
            "en": "英文",
            "fr": "法文",
            "es": "西班牙文",
            "de": "德文",
            "zh-CN": "简体中文",
            "zh-TW": "正体中文",
            "ja": "日文"
        }
        return lang_names.get(lang_code, lang_code)
    
    def _extract_new_abbreviations(
        self, 
        text: str, 
        existing: Dict[str, str]
    ):
        """从翻译文本中提取新的缩写解释"""
        # 匹配格式：缩写（全称，翻译）或 缩写(全称，翻译)
        pattern = r'([A-Z]{2,})\s*[（(]([^）)]+)[）)]'
        matches = re.findall(pattern, text)
        
        for abbr, explanation in matches:
            if abbr not in existing:
                existing[abbr] = explanation


# 全局翻译服务实例
translation_service = TranslationService()
