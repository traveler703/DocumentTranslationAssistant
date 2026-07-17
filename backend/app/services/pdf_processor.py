"""
PDF处理服务 - 提取和生成PDF
"""
import fitz  # PyMuPDF
import re
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
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        
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
                    bbox=tuple(line_data.get("bbox", (0, 0, 0, 0)))
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
            
            # 获取图片列表
            image_list = page.get_images()
            if not image_list:
                return None
            
            # 找到与bbox最接近的图片
            for img in image_list:
                xref = img[0]
                try:
                    base_image = self.doc.extract_image(xref)
                    image_data = base_image["image"]
                    
                    return ImageInfo(
                        xref=xref,
                        bbox=bbox,
                        page_num=page_num,
                        image_data=image_data,
                        width=base_image.get("width", 0),
                        height=base_image.get("height", 0)
                    )
                except Exception:
                    continue
            
            return None
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
        image_replacements: Optional[Dict[int, List[Tuple[Tuple, bytes]]]] = None  # page_num -> [(bbox, image_data)]
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
        output_doc = fitz.open()
        
        for page_num in range(len(self.source_doc)):
            # 复制原页面
            source_page = self.source_doc[page_num]
            new_page = output_doc.new_page(
                width=source_page.rect.width,
                height=source_page.rect.height
            )
            
            # 复制原页面内容
            new_page.show_pdf_page(new_page.rect, self.source_doc, page_num)
            
            # 应用文本翻译
            if page_num in translations:
                for bbox, text, style in translations[page_num]:
                    self._replace_text_region(new_page, bbox, text, style)
            
            # 应用图片替换
            if image_replacements and page_num in image_replacements:
                for bbox, image_data in image_replacements[page_num]:
                    self._replace_image_region(new_page, bbox, image_data)
        
        output_doc.save(output_path)
        output_doc.close()
        
        return output_path
    
    def _replace_text_region(
        self,
        page: fitz.Page,
        bbox: Tuple[float, float, float, float],
        text: str,
        style: dict
    ):
        """替换文本区域"""
        rect = fitz.Rect(bbox)
        
        # 用白色矩形覆盖原文本
        page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1))
        
        # 获取样式信息
        font_size = style.get("font_size", 11)
        font_name = style.get("font_name", "china-s")  # 使用内置中文字体
        color = style.get("color", (0, 0, 0))
        
        # 插入翻译文本
        # 自动调整字体大小以适应区域
        text_writer = fitz.TextWriter(page.rect)
        
        try:
            # 尝试使用文本适应区域的方式插入
            page.insert_textbox(
                rect,
                text,
                fontsize=font_size,
                fontname=font_name,
                color=color,
                align=fitz.TEXT_ALIGN_LEFT
            )
        except Exception:
            # 如果失败，使用简单的文本插入
            page.insert_text(
                (bbox[0], bbox[1] + font_size),
                text,
                fontsize=font_size,
                color=color
            )
    
    def _replace_image_region(
        self,
        page: fitz.Page,
        bbox: Tuple[float, float, float, float],
        image_data: bytes
    ):
        """替换图片区域"""
        rect = fitz.Rect(bbox)
        
        # 用白色覆盖原图片区域
        page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1))
        
        # 插入新图片
        page.insert_image(rect, stream=image_data)
    
    def create_bilingual_pdf(
        self,
        page_translations: Dict[int, str],  # page_num -> translated_text
        output_path: str
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
        
        for page_num in range(len(self.source_doc)):
            source_page = self.source_doc[page_num]
            
            # 创建新页面（宽度翻倍，左边原文，右边译文）
            new_width = source_page.rect.width * 2
            new_height = source_page.rect.height
            
            new_page = output_doc.new_page(width=new_width, height=new_height)
            
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
                    fontname="china-s"
                )
        
        output_doc.save(output_path)
        output_doc.close()
        
        return output_path
