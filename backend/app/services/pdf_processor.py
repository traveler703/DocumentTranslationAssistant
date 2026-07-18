"""
PDF处理服务 - 提取和生成PDF
"""
import fitz  # PyMuPDF
import re
import os
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import io
from dataclasses import dataclass, field

from app.config import settings


@dataclass
class TextSpan:
    """文本片段"""
    text: str
    bbox: Tuple[float, float, float, float]  # (x0, y0, x1, y1)
    font_name: str
    font_size: float
    color: int
    flags: int  # 字体标志（粗体、斜体等）


@dataclass
class TextLine:
    """文本行"""
    spans: List[TextSpan]
    bbox: Tuple[float, float, float, float]
    direction: Tuple[float, float] = (1.0, 0.0)
    writing_mode: int = 0
    
    @property
    def text(self) -> str:
        return "".join(span.text for span in self.spans)


@dataclass
class TextBlock:
    """文本块"""
    lines: List[TextLine]
    bbox: Tuple[float, float, float, float]
    block_type: str = "text"  # text, header, footer
    page_num: int = 0
    
    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


@dataclass
class ImageInfo:
    """图片信息"""
    xref: int
    bbox: Tuple[float, float, float, float]
    page_num: int
    image_data: bytes = field(default=b"", repr=False)
    width: int = 0
    height: int = 0


@dataclass
class PageLayout:
    """页面布局信息"""
    page_num: int
    width: float
    height: float
    text_blocks: List[TextBlock] = field(default_factory=list)
    images: List[ImageInfo] = field(default_factory=list)
    header_bbox: Optional[Tuple[float, float, float, float]] = None
    footer_bbox: Optional[Tuple[float, float, float, float]] = None
    columns: int = 1  # 栏数


class PDFProcessor:
    """PDF处理器"""
    
    def __init__(self, pdf_path: str):
        self.pdf_path = Path(pdf_path)
        self.doc = fitz.open(str(self.pdf_path))
        self.page_layouts: List[PageLayout] = []
        
    def close(self):
        """关闭文档"""
        self.doc.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    @property
    def page_count(self) -> int:
        return len(self.doc)
    
    def analyze_document(self) -> List[PageLayout]:
        """分析文档结构"""
        self.page_layouts = []
        
        for page_num in range(self.page_count):
            page = self.doc[page_num]
            layout = self._analyze_page(page, page_num)
            self.page_layouts.append(layout)
        
        # 处理跨页段落
        self._merge_cross_page_paragraphs()
        
        return self.page_layouts
    
    def _analyze_page(self, page: fitz.Page, page_num: int) -> PageLayout:
        """分析单页结构"""
        rect = page.rect
        layout = PageLayout(
            page_num=page_num,
            width=rect.width,
            height=rect.height
        )
        
        # 提取文本块
        text_flags = (
            fitz.TEXT_PRESERVE_WHITESPACE
            | fitz.TEXT_PRESERVE_LIGATURES
            | getattr(fitz, "TEXT_PRESERVE_IMAGES", 0)
        )
        blocks = page.get_text("dict", flags=text_flags)["blocks"]
        
        # 检测页眉页脚区域
        header_bbox, footer_bbox = self._detect_header_footer(blocks, rect.height)
        layout.header_bbox = header_bbox
        layout.footer_bbox = footer_bbox
        
        # 检测分栏
        layout.columns = self._detect_columns(blocks, rect.width)
        
        for block in blocks:
            if block["type"] == 0:  # 文本块
                text_block = self._parse_text_block(block, page_num)
                
                # 判断是否为页眉页脚
                if header_bbox and self._is_in_region(text_block.bbox, header_bbox):
                    text_block.block_type = "header"
                elif footer_bbox and self._is_in_region(text_block.bbox, footer_bbox):
                    text_block.block_type = "footer"
                
                layout.text_blocks.append(text_block)
                
            elif block["type"] == 1:  # 图片块
                image_info = self._extract_image_info(page, block, page_num)
                if image_info:
                    layout.images.append(image_info)
        
        # 按阅读顺序排序文本块（考虑分栏）
        layout.text_blocks = self._sort_blocks_by_reading_order(
            layout.text_blocks, layout.columns, rect.width
        )
        
        return layout
    
    def _parse_text_block(self, block: dict, page_num: int) -> TextBlock:
        """解析文本块"""
        lines = []
        for line_data in block.get("lines", []):
            spans = []
            for span_data in line_data.get("spans", []):
                span = TextSpan(
                    text=span_data.get("text", ""),
                    bbox=tuple(span_data.get("bbox", (0, 0, 0, 0))),
                    font_name=span_data.get("font", ""),
                    font_size=span_data.get("size", 12),
                    color=span_data.get("color", 0),
                    flags=span_data.get("flags", 0)
                )
                spans.append(span)
            
            if spans:
                line = TextLine(
                    spans=spans,
                    bbox=tuple(line_data.get("bbox", (0, 0, 0, 0))),
                    direction=tuple(line_data.get("dir", (1.0, 0.0))),
                    writing_mode=int(line_data.get("wmode", 0) or 0)
                )
                lines.append(line)
        
        return TextBlock(
            lines=lines,
            bbox=tuple(block.get("bbox", (0, 0, 0, 0))),
            page_num=page_num
        )
    
    def _extract_image_info(self, page: fitz.Page, block: dict, page_num: int) -> Optional[ImageInfo]:
        """提取图片信息"""
        try:
            bbox = tuple(block.get("bbox", (0, 0, 0, 0)))
            image_data = block.get("image", b"")
            if not image_data:
                return None

            # dict 图像块已经包含与 bbox 对应的原始图像。旧实现对每个块都
            # 返回页面的第一张图，导致图表被错误替换为 logo 或装饰图。
            return ImageInfo(
                xref=int(block.get("xref", 0) or 0),
                bbox=bbox,
                page_num=page_num,
                image_data=image_data,
                width=int(block.get("width", 0) or 0),
                height=int(block.get("height", 0) or 0)
            )
        except Exception:
            return None
    
    def _detect_header_footer(
        self, 
        blocks: List[dict], 
        page_height: float
    ) -> Tuple[Optional[Tuple], Optional[Tuple]]:
        """检测页眉页脚区域"""
        # 简单策略：页面顶部和底部一定区域内的文本可能是页眉页脚
        header_threshold = page_height * 0.08  # 顶部8%
        footer_threshold = page_height * 0.92  # 底部8%
        
        header_bbox = None
        footer_bbox = None
        
        for block in blocks:
            if block["type"] != 0:
                continue
            bbox = block.get("bbox", (0, 0, 0, 0))
            
            # 检测页眉
            if bbox[3] < header_threshold:  # y1 < threshold
                if header_bbox is None:
                    header_bbox = (0, 0, float('inf'), header_threshold)
            
            # 检测页脚
            if bbox[1] > footer_threshold:  # y0 > threshold
                if footer_bbox is None:
                    footer_bbox = (0, footer_threshold, float('inf'), page_height)
        
        return header_bbox, footer_bbox
    
    def _detect_columns(self, blocks: List[dict], page_width: float) -> int:
        """检测页面分栏数"""
        text_blocks = [b for b in blocks if b["type"] == 0]
        if len(text_blocks) < 4:
            return 1
        
        # 收集所有文本块的x坐标
        x_positions = []
        for block in text_blocks:
            bbox = block.get("bbox", (0, 0, 0, 0))
            x_center = (bbox[0] + bbox[2]) / 2
            x_positions.append(x_center)
        
        # 简单判断：如果有明显的左右两组x坐标，则为双栏
        left_count = sum(1 for x in x_positions if x < page_width * 0.4)
        right_count = sum(1 for x in x_positions if x > page_width * 0.6)
        
        if left_count > 2 and right_count > 2:
            return 2
        
        return 1
    
    def _is_in_region(
        self, 
        bbox: Tuple[float, float, float, float],
        region: Tuple[float, float, float, float]
    ) -> bool:
        """判断bbox是否在region内"""
        return (bbox[1] >= region[1] and bbox[3] <= region[3])
    
    def _sort_blocks_by_reading_order(
        self, 
        blocks: List[TextBlock],
        columns: int,
        page_width: float
    ) -> List[TextBlock]:
        """按阅读顺序排序文本块"""
        if columns == 1:
            # 单栏：按y坐标排序
            return sorted(blocks, key=lambda b: (b.bbox[1], b.bbox[0]))
        
        # 多栏：先分组，再各组内排序
        column_width = page_width / columns
        column_blocks = [[] for _ in range(columns)]
        
        for block in blocks:
            x_center = (block.bbox[0] + block.bbox[2]) / 2
            col_idx = min(int(x_center / column_width), columns - 1)
            column_blocks[col_idx].append(block)
        
        # 各栏内按y坐标排序
        for i in range(columns):
            column_blocks[i].sort(key=lambda b: b.bbox[1])
        
        # 合并各栏
        result = []
        for col in column_blocks:
            result.extend(col)
        
        return result
    
    def _merge_cross_page_paragraphs(self):
        """合并跨页段落"""
        for i in range(len(self.page_layouts) - 1):
            current_page = self.page_layouts[i]
            next_page = self.page_layouts[i + 1]
            
            # 获取当前页最后一个正文块
            current_text_blocks = [
                b for b in current_page.text_blocks 
                if b.block_type == "text"
            ]
            if not current_text_blocks:
                continue
            
            last_block = current_text_blocks[-1]
            last_text = last_block.text.strip()
            
            # 获取下一页第一个正文块
            next_text_blocks = [
                b for b in next_page.text_blocks 
                if b.block_type == "text"
            ]
            if not next_text_blocks:
                continue
            
            first_block = next_text_blocks[0]
            first_text = first_block.text.strip()
            
            # 判断是否需要合并
            # 1. 当前页最后一个字符不是句末标点
            # 2. 下一页第一个字符是小写字母
            if last_text and first_text:
                ends_sentence = last_text[-1] in '.!?。！？'
                starts_lowercase = first_text[0].islower()
                
                if not ends_sentence or starts_lowercase:
                    # 标记为跨页段落
                    last_block.block_type = "text_continued"
                    first_block.block_type = "text_continuation"
    
    def get_all_text(self, include_header_footer: bool = False) -> str:
        """获取所有文本"""
        if not self.page_layouts:
            self.analyze_document()
        
        texts = []
        for layout in self.page_layouts:
            for block in layout.text_blocks:
                if not include_header_footer and block.block_type in ("header", "footer"):
                    continue
                texts.append(block.text)
        
        return "\n\n".join(texts)
    
    def get_text_by_page(self, page_num: int, include_header_footer: bool = False) -> str:
        """获取指定页的文本"""
        if not self.page_layouts:
            self.analyze_document()
        
        if page_num < 0 or page_num >= len(self.page_layouts):
            return ""
        
        layout = self.page_layouts[page_num]
        texts = []
        for block in layout.text_blocks:
            if not include_header_footer and block.block_type in ("header", "footer"):
                continue
            texts.append(block.text)
        
        return "\n\n".join(texts)
    
    def get_images(self) -> List[ImageInfo]:
        """获取所有图片"""
        if not self.page_layouts:
            self.analyze_document()
        
        images = []
        for layout in self.page_layouts:
            images.extend(layout.images)
        
        return images


class PDFGenerator:
    """PDF生成器 - 保持原有布局的翻译PDF"""
    
    def __init__(self, source_pdf_path: str):
        self.source_path = Path(source_pdf_path)
        self.source_doc = fitz.open(str(self.source_path))
        self.layout_warnings: List[str] = []
    
    def close(self):
        """关闭文档"""
        self.source_doc.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def create_translated_pdf(
        self,
        translations: Dict[int, List[Tuple[Tuple, str, dict]]],  # page_num -> [(bbox, translated_text, style)]
        output_path: str,
        image_replacements: Optional[Dict[int, List[Tuple[Tuple, bytes]]]] = None,  # page_num -> [(bbox, image_data)]
        target_lang: str = "zh-CN"
    ) -> str:
        """
        创建翻译后的PDF
        
        Args:
            translations: 每页的翻译内容 {页码: [(bbox, 翻译文本, 样式), ...]}
            output_path: 输出文件路径
            image_replacements: 替换的图片 {页码: [(bbox, 图片数据), ...]}
        
        Returns:
            输出文件路径
        """
        font_name, font_path = self._resolve_output_font(target_lang)
        self.layout_warnings = []
        output_path_obj = Path(output_path)
        output_path_obj.parent.mkdir(parents=True, exist_ok=True)

        # 直接克隆源文档再修改，保留 metadata、书签、链接和页面标注。
        source_bytes = self.source_doc.tobytes(garbage=3, deflate=True)
        output_doc = fitz.open(stream=source_bytes, filetype="pdf")

        try:
            for page_num, page in enumerate(output_doc):
                # 位图先替换；随后写入 PDF 文本层，避免整页扫描图盖住译文。
                if image_replacements and page_num in image_replacements:
                    for bbox, image_data in image_replacements[page_num]:
                        self._replace_image_region(page, bbox, image_data)

                page_translations = translations.get(page_num, [])
                if not page_translations:
                    continue

                prepared = self._prepare_page_translations(
                    page,
                    page_translations,
                    font_name,
                    font_path
                )
                prepared = self._validate_translations_on_page_clone(
                    page,
                    prepared,
                    font_name,
                    font_path
                )
                if not prepared:
                    continue
                original_links = page.get_links()

                # 只有在译文确认能放入文本框之后，才移除对应原文。
                for item in prepared:
                    redaction_bboxes = item["style"].get(
                        "redaction_bboxes", [item["bbox"]]
                    )
                    for redaction_bbox in redaction_bboxes:
                        rect = fitz.Rect(redaction_bbox)
                        if not rect.is_empty and not rect.is_infinite:
                            page.add_redact_annot(rect, fill=False)

                self._apply_redactions_preserving_graphics(page)
                # Redaction 会重写页面内容流。此后再注册字体，避免未使用的
                # 字体资源在清理内容流时被移除，导致预检和实际写入不一致。
                if font_path:
                    page.insert_font(fontname=font_name, fontfile=font_path)
                self._restore_page_links(page, original_links)

                for item in prepared:
                    inserted = self._insert_translation_item(
                        page, item, font_name, font_path
                    )
                    if inserted < 0:
                        # 克隆页预演后通常不会进入这里。若底层 PDF 状态仍出现
                        # 非确定性差异，立即从源 PDF 覆盖恢复这个文本块，而不是
                        # 留下空白区域或让整份文档失败。
                        restored = self._restore_original_item(
                            page, page_num, item
                        )
                        action = "已恢复原文" if restored else "恢复原文失败"
                        self.layout_warnings.append(
                            f"第 {page_num + 1} 页有 1 个译文块实际写入失败，"
                            f"{action}：{item['text'][:40]!r}"
                        )

            output_doc.save(
                str(output_path_obj),
                garbage=4,
                deflate=True,
                clean=True
            )
        finally:
            output_doc.close()
        
        return output_path

    def _prepare_page_translations(
        self,
        page: fitz.Page,
        translations: List[Tuple[Tuple, str, dict]],
        font_name: str,
        font_path: Optional[str]
    ) -> List[dict]:
        """在空白临时页上预排版，返回确认能够完整落版的项目。"""
        scratch_doc = fitz.open()
        scratch_page = scratch_doc.new_page(
            width=page.rect.width,
            height=page.rect.height
        )
        try:
            if font_path:
                scratch_page.insert_font(fontname=font_name, fontfile=font_path)

            prepared: List[dict] = []
            for bbox, text, style in translations:
                clean_text = self._clean_translation(text)
                rect = fitz.Rect(bbox)
                if not clean_text or rect.is_empty or rect.is_infinite:
                    continue
                original_size = float(style.get("font_size", 11))
                layout_rect = self._expand_layout_rect(
                    rect, page.rect, original_size
                )

                fitted_size = self._fit_font_size(
                    scratch_page,
                    layout_rect,
                    clean_text,
                    font_name,
                    font_path,
                    original_size,
                    style.get("color", (0, 0, 0)),
                    float(style.get("lineheight", 1.0)),
                    int(style.get("rotation", 0))
                )
                if fitted_size is None:
                    # 单个极端标签不应让整份文档失败。该区域不加入 redaction，
                    # 因而会安全保留原文，并通过任务提示告知用户。
                    self.layout_warnings.append(
                        f"第 {page.number + 1} 页有 1 个极小标签无法安全排版，"
                        f"已保留原文：{clean_text[:40]!r}"
                    )
                    continue
                prepared.append({
                    "bbox": bbox,
                    "layout_bbox": tuple(layout_rect),
                    "text": clean_text,
                    "style": style,
                    "font_size": fitted_size
                })
            return prepared
        finally:
            scratch_doc.close()

    def _validate_translations_on_page_clone(
        self,
        page: fitz.Page,
        prepared: List[dict],
        font_name: str,
        font_path: Optional[str]
    ) -> List[dict]:
        """
        在真实页面的克隆上执行完整写入流程。

        空白页上的字体拟合无法覆盖原页面的内容流、字体资源和 redaction
        重写行为。这里会逐轮排除真实预演仍失败的单个文本块；失败块没有在
        正式页面上添加 redaction，因此原文会被完整保留。
        """
        candidates = list(prepared)
        while candidates:
            scratch_doc = fitz.open()
            try:
                scratch_doc.insert_pdf(
                    page.parent,
                    from_page=page.number,
                    to_page=page.number,
                    links=False,
                    annots=False
                )
                scratch_page = scratch_doc[0]
                for item in candidates:
                    for redaction_bbox in item["style"].get(
                        "redaction_bboxes", [item["bbox"]]
                    ):
                        rect = fitz.Rect(redaction_bbox)
                        if not rect.is_empty and not rect.is_infinite:
                            scratch_page.add_redact_annot(rect, fill=False)

                self._apply_redactions_preserving_graphics(scratch_page)
                if font_path:
                    scratch_page.insert_font(
                        fontname=font_name, fontfile=font_path
                    )

                failed_indexes = [
                    index
                    for index, item in enumerate(candidates)
                    if self._insert_translation_item(
                        scratch_page, item, font_name, font_path
                    ) < 0
                ]
            finally:
                scratch_doc.close()

            if not failed_indexes:
                return candidates

            failed_set = set(failed_indexes)
            for index in failed_indexes:
                item = candidates[index]
                self.layout_warnings.append(
                    f"第 {page.number + 1} 页有 1 个译文块在真实页面预演中"
                    f"无法安全排版，已保留原文：{item['text'][:40]!r}"
                )
            candidates = [
                item
                for index, item in enumerate(candidates)
                if index not in failed_set
            ]

        return []

    @staticmethod
    def _insert_translation_item(
        page: fitz.Page,
        item: dict,
        font_name: str,
        font_path: Optional[str]
    ) -> float:
        """用与正式写入完全相同的参数插入一个译文块。"""
        return page.insert_textbox(
            fitz.Rect(item["layout_bbox"]),
            item["text"],
            fontsize=item["font_size"],
            fontname=font_name,
            fontfile=font_path,
            color=item["style"].get("color", (0, 0, 0)),
            align=fitz.TEXT_ALIGN_LEFT,
            lineheight=item["style"].get("lineheight", 1.0),
            rotate=item["style"].get("rotation", 0),
            overlay=True
        )

    def _restore_original_item(
        self,
        page: fitz.Page,
        page_num: int,
        item: dict
    ) -> bool:
        """实际写入意外失败时，从源 PDF 精确覆盖恢复对应原文区域。"""
        try:
            restored_any = False
            for redaction_bbox in item["style"].get(
                "redaction_bboxes", [item["bbox"]]
            ):
                rect = fitz.Rect(redaction_bbox)
                if rect.is_empty or rect.is_infinite:
                    continue
                page.show_pdf_page(
                    rect,
                    self.source_doc,
                    page_num,
                    clip=rect,
                    keep_proportion=False,
                    overlay=True
                )
                restored_any = True
            return restored_any
        except Exception:
            return False

    @staticmethod
    def _expand_layout_rect(
        rect: fitz.Rect,
        page_rect: fitz.Rect,
        font_size: float
    ) -> fitz.Rect:
        """
        PDF 提取的 bbox 是字形边界，不是可直接排版的行框。
        增加很小的行高余量，避免同字号文本因字体升降部差异被误判溢出。
        """
        padding = max(1.0, min(3.0, font_size * 0.3))
        expanded = fitz.Rect(
            rect.x0 - 0.5,
            rect.y0 - padding * 0.25,
            rect.x1 + 0.5,
            rect.y1 + padding
        )
        return expanded & page_rect

    @staticmethod
    def _fit_font_size(
        page: fitz.Page,
        rect: fitz.Rect,
        text: str,
        font_name: str,
        font_path: Optional[str],
        original_size: float,
        color: Tuple[float, float, float],
        lineheight: float,
        rotation: int
    ) -> Optional[float]:
        """逐级缩小字号，直到 PyMuPDF 确认文本框可完整容纳译文。"""
        start = max(4.0, min(36.0, original_size))
        minimum = max(3.0, min(6.0, start * 0.5))
        size = start
        while size >= minimum - 0.01:
            shape = page.new_shape()
            remaining = shape.insert_textbox(
                rect,
                text,
                fontsize=size,
                fontname=font_name,
                fontfile=font_path,
                color=color,
                align=fitz.TEXT_ALIGN_LEFT,
                lineheight=lineheight,
                rotate=rotation
            )
            if remaining >= 0:
                # 不向上取整临界字号，并额外留出 0.05pt 的排版余量。
                # PyMuPDF 在真实页面重写内容流后的浮点计算可能略有差异。
                return max(minimum, size - 0.05)
            size -= 0.5
        return None

    @staticmethod
    def _apply_redactions_preserving_graphics(page: fitz.Page):
        """移除文本但保留与文本框相交的图片和表格线。"""
        try:
            page.apply_redactions(images=0, graphics=0)
        except TypeError:
            # PyMuPDF 1.23 尚不支持 graphics 参数。
            page.apply_redactions(images=0)

    @staticmethod
    def _restore_page_links(page: fitz.Page, original_links: List[dict]):
        """
        PyMuPDF 会删除与 redaction 区域相交的链接。
        清空幸存链接后按处理前快照重建，避免目录和文献 URL 失效。
        """
        for link in page.get_links():
            page.delete_link(link)

        allowed_keys = {
            "kind", "from", "page", "to", "file", "uri",
            "zoom", "name", "nameddest"
        }
        for link in original_links:
            restored = {
                key: value
                for key, value in link.items()
                if key in allowed_keys and value is not None
            }
            page.insert_link(restored)

    @staticmethod
    def _clean_translation(text: str) -> str:
        """清除模型偶尔附带的代码围栏和空白，不改变正文内容。"""
        cleaned = text.strip()
        if cleaned.startswith("```") and cleaned.endswith("```"):
            cleaned = re.sub(r"^```[^\n]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)
        return cleaned.strip()

    @staticmethod
    def _resolve_output_font(target_lang: str) -> Tuple[str, Optional[str]]:
        """选择并显式嵌入支持目标语言的字体，杜绝未定义 CID 字体。"""
        if target_lang not in {"zh-CN", "zh-TW", "ja"}:
            return "helv", None

        configured = getattr(settings, "CJK_FONT_PATH", None)
        candidates = [
            configured,
            os.getenv("DTA_CJK_FONT_PATH"),
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/msgothic.ttc",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return "DTAUnicode", str(candidate)

        raise RuntimeError(
            "未找到支持中文/日文的 Unicode 字体。请设置 CJK_FONT_PATH "
            "或 DTA_CJK_FONT_PATH（推荐 Noto Sans CJK）。"
        )
    
    def _replace_image_region(
        self,
        page: fitz.Page,
        bbox: Tuple[float, float, float, float],
        image_data: bytes
    ):
        """替换图片区域"""
        rect = fitz.Rect(bbox)
        
        # 新图片本身是不透明位图，直接覆盖可避免先画白框造成边缘白线。
        page.insert_image(rect, stream=image_data, overlay=True)
    
    def create_bilingual_pdf(
        self,
        page_translations: Dict[int, str],  # page_num -> translated_text
        output_path: str,
        target_lang: str = "zh-CN"
    ) -> str:
        """
        创建双语对照PDF（原文+译文）
        
        Args:
            page_translations: 每页的翻译文本
            output_path: 输出文件路径
        
        Returns:
            输出文件路径
        """
        output_doc = fitz.open()
        font_name, font_path = self._resolve_output_font(target_lang)
        
        for page_num in range(len(self.source_doc)):
            source_page = self.source_doc[page_num]
            
            # 创建新页面（宽度翻倍，左边原文，右边译文）
            new_width = source_page.rect.width * 2
            new_height = source_page.rect.height
            
            new_page = output_doc.new_page(width=new_width, height=new_height)
            if font_path:
                new_page.insert_font(fontname=font_name, fontfile=font_path)
            
            # 左边放原文
            left_rect = fitz.Rect(0, 0, source_page.rect.width, new_height)
            new_page.show_pdf_page(left_rect, self.source_doc, page_num)
            
            # 右边放译文
            if page_num in page_translations:
                right_rect = fitz.Rect(
                    source_page.rect.width + 20,
                    20,
                    new_width - 20,
                    new_height - 20
                )
                new_page.insert_textbox(
                    right_rect,
                    page_translations[page_num],
                    fontsize=11,
                    fontname=font_name
                )
        
        output_doc.save(output_path)
        output_doc.close()
        
        return output_path
