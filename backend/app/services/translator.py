"""
翻译服务 - 协调PDF处理、LLM翻译和图片处理
支持并行翻译和token优化
"""
import asyncio
import uuid
import re
import logging
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

logger = logging.getLogger(__name__)


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
    warnings: List[str] = field(default_factory=list)


@dataclass
class PageBatch:
    """页面批次"""
    page_nums: List[int]
    layouts: List[PageLayout]
    items: List["TranslationUnit"]


@dataclass
class TranslationUnit:
    """一个具有稳定 ID 的 PDF 文本块"""
    unit_id: str
    page_num: int
    block_idx: int
    block: TextBlock


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
            has_ocr_candidates = any(
                (image.bbox[2] - image.bbox[0]) >= 30
                and (image.bbox[3] - image.bbox[1]) >= 15
                for image in images
            )
            if has_ocr_candidates and not self.image_processor.ocr_available:
                self._add_task_warning(
                    task, self.image_processor.ocr_unavailable_reason
                )
            image_replacements = await self._translate_images_parallel(
                images, llm_client, task
            )
            if has_ocr_candidates and not self.image_processor.ocr_available:
                self._add_task_warning(
                    task, self.image_processor.ocr_unavailable_reason
                )
            
            # 7. 生成翻译后的PDF
            task.message = "正在生成翻译文档..."
            task.progress = 95
            
            with PDFGenerator(str(task.source_path)) as generator:
                generator.create_translated_pdf(
                    translations=page_translations,
                    output_path=str(task.output_path),
                    image_replacements=image_replacements if image_replacements else None,
                    target_lang=task.target_lang
                )
                for warning in generator.layout_warnings:
                    self._add_task_warning(task, warning)
            
            task.status = TranslationStatus.COMPLETED
            task.progress = 100
            if task.warnings:
                task.message = (
                    f"翻译完成（{len(task.warnings)} 项提示："
                    f"{task.warnings[0]}）"
                )
            else:
                task.message = "翻译完成"
            
        except Exception as e:
            task.status = TranslationStatus.FAILED
            task.error = str(e)
            task.message = f"翻译失败: {str(e)}"
            # 这是 FastAPI BackgroundTasks 中运行的任务。状态和错误已经写入
            # task，继续抛出只会制造 “Exception in ASGI application”，
            # 并不会让前端得到更多信息。
            logger.exception("Translation task %s failed", task.task_id)
        
        finally:
            if hasattr(llm_client, 'close'):
                try:
                    await llm_client.close()
                except Exception:
                    logger.exception(
                        "Failed to close LLM client for task %s", task.task_id
                    )
    
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
        """
        创建带稳定块 ID 的翻译批次。

        页眉、页脚、目录、表格和图中的矢量文字都属于普通 PDF 文本块，
        因此不能像旧实现那样排除页眉页脚，也不能依赖双换行拆回段落。
        """
        batches: List[PageBatch] = []
        current_pages: List[int] = []
        current_layouts: List[PageLayout] = []
        current_items: List[TranslationUnit] = []
        current_text_len = 0

        def flush():
            nonlocal current_pages, current_layouts, current_items, current_text_len
            if current_items:
                batches.append(PageBatch(
                    page_nums=current_pages,
                    layouts=current_layouts,
                    items=current_items
                ))
            current_pages = []
            current_layouts = []
            current_items = []
            current_text_len = 0
        
        for page_num, layout in enumerate(page_layouts):
            for block_idx, block in enumerate(layout.text_blocks):
                render_blocks = self._split_fragmented_block(block)
                for part_idx, render_block in enumerate(render_blocks):
                    text = render_block.text.strip()
                    if not text or not self._should_translate(text):
                        continue

                    starts_new_page = page_num not in current_pages
                    exceeds_page_limit = (
                        starts_new_page
                        and len(current_pages) >= BATCH_SIZE_PAGES
                    )
                    exceeds_text_limit = (
                        current_items
                        and current_text_len + len(text) > MAX_TEXT_PER_BATCH
                    )
                    if exceeds_page_limit or exceeds_text_limit:
                        flush()

                    if page_num not in current_pages:
                        current_pages.append(page_num)
                        current_layouts.append(layout)

                    suffix = (
                        f"-l{part_idx + 1}" if len(render_blocks) > 1 else ""
                    )
                    current_items.append(TranslationUnit(
                        unit_id=(
                            f"p{page_num + 1}-b{block_idx + 1}{suffix}"
                        ),
                        page_num=page_num,
                        block_idx=block_idx,
                        block=render_block
                    ))
                    current_text_len += len(text)

        flush()
        
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
        completed_pages: set[int] = set()
        progress_lock = asyncio.Lock()
        
        async def translate_batch(batch: PageBatch) -> Dict[int, List[Tuple[Tuple, str, dict]]]:
            async with semaphore:
                result = await self._translate_single_batch(batch, llm_client, task)
                async with progress_lock:
                    completed_pages.update(batch.page_nums)
                    task.current_page = len(completed_pages)
                    task.progress = (
                        len(completed_pages) / max(1, task.total_pages)
                    ) * 90
                    task.message = (
                        f"已翻译 {len(completed_pages)}/{task.total_pages} 页..."
                    )
                    if progress_callback:
                        progress_callback(task.progress, task.message)
                return result
        
        # 并行执行所有批次
        results = await asyncio.gather(*[translate_batch(b) for b in batches])
        
        # 合并结果
        for result in results:
            for page_num, entries in result.items():
                page_translations.setdefault(page_num, []).extend(entries)
        
        return page_translations
    
    async def _translate_single_batch(
        self,
        batch: PageBatch,
        llm_client: BaseLLMClient,
        task: TranslationTask
    ) -> Dict[int, List[Tuple[Tuple, str, dict]]]:
        """按稳定 ID 翻译单个批次，确保每个块均有明确映射。"""
        result: Dict[int, List[Tuple[Tuple, str, dict]]] = {}
        
        if not batch.items:
            for page_num in batch.page_nums:
                result[page_num] = []
            return result

        translated_by_id = await llm_client.translate_segments(
            [
                {"id": item.unit_id, "text": item.block.text.strip()}
                for item in batch.items
            ],
            self._get_lang_name(task.source_lang),
            self._get_lang_name(task.target_lang),
            abbreviations=task.abbreviations
        )

        for page_num in batch.page_nums:
            result.setdefault(page_num, [])

        for item in batch.items:
            translated = translated_by_id.get(item.unit_id, "").strip()
            if not translated:
                # BaseLLMClient 会重试缺失项；这里仍保留最后一道防线。
                translated = item.block.text
            translated = await self._compact_translation_if_needed(
                item,
                translated,
                llm_client,
                task
            )
            # 模型名、公式、引用编号等经常无需翻译。若内容仅有空白差异，
            # 不要重绘：保留原 PDF 字体可以避免数学字母等罕见字符变成方框。
            if self._normalized_render_text(translated) == (
                self._normalized_render_text(item.block.text)
            ):
                continue
            style = self._extract_text_style(item.block)
            result[item.page_num].append(
                (item.block.bbox, translated, style)
            )
        
        return result
    
    async def _translate_images_parallel(
        self,
        images: List,
        llm_client: BaseLLMClient,
        task: TranslationTask
    ) -> Dict[int, List[Tuple[Tuple, bytes]]]:
        """并行处理图片翻译"""
        image_replacements: Dict[int, List[Tuple[Tuple, bytes]]] = {}
        
        if not images or not self.image_processor.ocr_available:
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

                    display_width = image_info.bbox[2] - image_info.bbox[0]
                    display_height = image_info.bbox[3] - image_info.bbox[1]
                    # 论文中的 logo / 图标常以高分辨率小图片嵌入。对这些
                    # 逐个 OCR 会产生大量噪声；可读标签通常已在 PDF 文本层。
                    if display_width < 30 or display_height < 15:
                        continue
                    
                    # 提取图片中的文字
                    text_regions = self.image_processor.extract_text_from_image(
                        image_info.image_data
                    )
                    
                    if not text_regions:
                        continue
                    
                    # 批量翻译图片中的所有文字（减少API调用）
                    if not any(r.text.strip() for r in text_regions):
                        continue
                    
                    segments = [
                        {
                            "id": f"img-p{page_num + 1}-x{image_info.xref}-r{i + 1}",
                            "text": region.text
                        }
                        for i, region in enumerate(text_regions)
                        if region.text.strip()
                    ]
                    translated_by_id = await llm_client.translate_segments(
                        segments,
                        self._get_lang_name(task.source_lang),
                        self._get_lang_name(task.target_lang),
                        abbreviations=task.abbreviations
                    )

                    text_replacements = []
                    valid_regions = [r for r in text_regions if r.text.strip()]
                    for i, region in enumerate(valid_regions):
                        segment_id = (
                            f"img-p{page_num + 1}-x{image_info.xref}-r{i + 1}"
                        )
                        translated = translated_by_id.get(segment_id, "").strip()
                        if translated:
                            text_replacements.append((region, translated))
                    
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

    async def _compact_translation_if_needed(
        self,
        item: TranslationUnit,
        translated: str,
        llm_client: BaseLLMClient,
        task: TranslationTask
    ) -> str:
        """狭小图表/表格标签若被扩写，则请求一次不展开缩写的短译文。"""
        char_limit = self._estimate_compact_char_limit(item.block)
        translated_len = len(re.sub(r"\s+", "", translated))
        if char_limit >= 80 or translated_len <= char_limit:
            return translated

        try:
            compact = await llm_client.translate(
                item.block.text.strip(),
                self._get_lang_name(task.source_lang),
                self._get_lang_name(task.target_lang),
                context=(
                    "这是图表、目录或表格中的狭小标签。请给出最短且准确的译法；"
                    "不要展开或解释缩写，通用缩写和模型名可直接保留；"
                    f"最多 {char_limit} 个目标语言字符，只输出标签本身。"
                ),
                abbreviations=None
            )
        except Exception:
            # 精简是补救步骤，不能因为一次额外请求失败而丢掉整批已完成译文。
            return translated
        compact = self._clean_compact_translation(compact)
        if compact and len(re.sub(r"\s+", "", compact)) < translated_len:
            return compact
        return translated

    def _estimate_compact_char_limit(self, block: TextBlock) -> int:
        """根据文本方向、字号和框尺寸估算无需降到不可读字号的字符容量。"""
        style = self._extract_text_style(block)
        x0, y0, x1, y1 = block.bbox
        width = max(1.0, x1 - x0)
        height = max(1.0, y1 - y0)
        font_size = max(3.0, float(style.get("font_size", 11)))
        rotation = int(style.get("rotation", 0))

        if rotation in (90, 270):
            line_width, cross_size = height, width
        else:
            line_width, cross_size = width, height

        visual_lines = max(
            1,
            len(block.lines),
            int(cross_size / max(3.0, font_size * 0.85))
        )
        estimated = int(
            (line_width / max(3.0, font_size * 0.6)) * visual_lines
        )
        source_len = len(re.sub(r"\s+", "", block.text))
        return max(2, min(80, max(source_len, estimated)))

    @staticmethod
    def _clean_compact_translation(text: str) -> str:
        """清除短译重试中模型偶尔添加的围栏、引号或说明前缀。"""
        cleaned = text.strip()
        cleaned = re.sub(r"^```[^\n]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
        cleaned = re.sub(
            r"^(?:翻译|译文|短译)\s*[:：]\s*",
            "",
            cleaned,
            flags=re.IGNORECASE
        )
        return cleaned.strip().strip("\"'“”‘’")

    @staticmethod
    def _normalized_render_text(text: str) -> str:
        """仅忽略排版空白，用于判断模型是否实质修改了文本。"""
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _add_task_warning(task: TranslationTask, warning: str):
        if warning and warning not in task.warnings:
            task.warnings.append(warning)
    
    def _extract_text_style(self, block: TextBlock) -> dict:
        """提取文本样式"""
        style = {
            "font_size": 11,
            "color": (0, 0, 0)
        }
        
        if block.lines and block.lines[0].spans:
            first_span = block.lines[0].spans[0]
            style["font_size"] = max(4, min(36, first_span.font_size))
            
            # 转换颜色
            color_int = first_span.color
            r = (color_int >> 16) & 0xFF
            g = (color_int >> 8) & 0xFF
            b = color_int & 0xFF
            style["color"] = (r / 255, g / 255, b / 255)

        # 逐行移除原文字，尽量避免擦掉表格边框、图形和底纹。
        style["redaction_bboxes"] = [line.bbox for line in block.lines]
        style["block_type"] = block.block_type
        centers = sorted({
            round((line.bbox[1] + line.bbox[3]) / 2, 2)
            for line in block.lines
        })
        if len(centers) > 1:
            gaps = sorted(
                centers[index + 1] - centers[index]
                for index in range(len(centers) - 1)
            )
            median_gap = gaps[len(gaps) // 2]
            style["lineheight"] = max(
                0.8,
                min(1.3, median_gap / max(1.0, style["font_size"]))
            )
        else:
            style["lineheight"] = 1.0
        if block.lines:
            direction_x, direction_y = block.lines[0].direction
            if abs(direction_x) >= abs(direction_y):
                style["rotation"] = 0 if direction_x >= 0 else 180
            else:
                style["rotation"] = 90 if direction_y < 0 else 270
        
        return style

    @staticmethod
    def _should_translate(text: str) -> bool:
        """过滤纯数字/符号，保留所有包含自然语言字符的块。"""
        # 标识符不是自然语言；交给模型反而容易破坏版本号和分类代码。
        if re.match(r"^\s*arxiv\s*:\s*\d", text, re.IGNORECASE):
            return False
        if re.fullmatch(r"\s*(?:https?://|www\.)\S+\s*", text, re.IGNORECASE):
            return False
        return bool(re.search(r"[A-Za-z\u00C0-\u024F\u3040-\u30FF\u3400-\u9FFF]", text))

    @staticmethod
    def _split_fragmented_block(block: TextBlock) -> List[TextBlock]:
        """
        将目录制表位、表格多列等“同一视觉行的多个 PDF line”拆开。

        普通正文仍按完整段落翻译以保留上下文；只有检测到同一 y 坐标上
        存在多个独立 line 时才逐片段映射，从而保住目录页码和表格列位置。
        """
        lines = block.lines
        has_rotated_lines = any(
            abs(line.direction[1]) > abs(line.direction[0])
            for line in lines
        )
        fragmented = (has_rotated_lines and len(lines) > 1) or any(
            abs(
                ((left.bbox[1] + left.bbox[3]) / 2)
                - ((right.bbox[1] + right.bbox[3]) / 2)
            )
            <= max(1.0, min(
                left.bbox[3] - left.bbox[1],
                right.bbox[3] - right.bbox[1]
            ) * 0.35)
            for index, left in enumerate(lines)
            for right in lines[index + 1:]
        )
        if not fragmented:
            return [block]

        return [
            TextBlock(
                lines=[line],
                bbox=line.bbox,
                block_type=block.block_type,
                page_num=block.page_num
            )
            for line in lines
        ]
    
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
