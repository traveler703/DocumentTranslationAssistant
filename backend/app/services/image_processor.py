"""
图片处理服务 - 处理PDF中的图片文字翻译
"""
import io
from typing import List, Dict, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont
import pytesseract
from dataclasses import dataclass


@dataclass
class TextRegion:
    """图片中的文字区域"""
    text: str
    bbox: Tuple[int, int, int, int]  # (x, y, width, height)
    confidence: float


class ImageProcessor:
    """图片处理器"""
    
    def __init__(self, tesseract_lang: str = "eng"):
        """
        初始化图片处理器
        
        Args:
            tesseract_lang: Tesseract OCR语言代码
        """
        self.tesseract_lang = tesseract_lang
        # 语言代码映射
        self.lang_map = {
            "en": "eng",
            "fr": "fra",
            "es": "spa",
            "de": "deu",
            "zh-CN": "chi_sim",
            "zh-TW": "chi_tra",
            "ja": "jpn"
        }
    
    def set_source_language(self, lang_code: str):
        """设置源语言"""
        self.tesseract_lang = self.lang_map.get(lang_code, "eng")
    
    def extract_text_from_image(self, image_data: bytes) -> List[TextRegion]:
        """
        从图片中提取文字区域
        
        Args:
            image_data: 图片二进制数据
        
        Returns:
            文字区域列表
        """
        # 打开图片
        image = Image.open(io.BytesIO(image_data))
        
        # 使用Tesseract进行OCR，获取详细信息
        try:
            data = pytesseract.image_to_data(
                image, 
                lang=self.tesseract_lang,
                output_type=pytesseract.Output.DICT
            )
        except Exception as e:
            print(f"OCR error: {e}")
            return []
        
        # 解析结果
        regions = []
        n_boxes = len(data['text'])
        
        current_line_text = []
        current_line_bbox = None
        
        for i in range(n_boxes):
            text = data['text'][i].strip()
            conf = float(data['conf'][i])
            
            if text and conf > 30:  # 置信度阈值
                x, y, w, h = (
                    data['left'][i],
                    data['top'][i],
                    data['width'][i],
                    data['height'][i]
                )
                
                region = TextRegion(
                    text=text,
                    bbox=(x, y, w, h),
                    confidence=conf
                )
                regions.append(region)
        
        # 合并相邻的文字区域为行
        merged_regions = self._merge_text_regions(regions)
        
        return merged_regions
    
    def _merge_text_regions(self, regions: List[TextRegion]) -> List[TextRegion]:
        """合并相邻的文字区域"""
        if not regions:
            return []
        
        # 按y坐标分组（同一行）
        sorted_regions = sorted(regions, key=lambda r: (r.bbox[1], r.bbox[0]))
        
        merged = []
        current_line = [sorted_regions[0]]
        
        for region in sorted_regions[1:]:
            last_region = current_line[-1]
            
            # 判断是否同一行（y坐标差距小于高度的一半）
            y_diff = abs(region.bbox[1] - last_region.bbox[1])
            avg_height = (region.bbox[3] + last_region.bbox[3]) / 2
            
            if y_diff < avg_height * 0.5:
                current_line.append(region)
            else:
                # 合并当前行
                merged.append(self._merge_line(current_line))
                current_line = [region]
        
        # 合并最后一行
        if current_line:
            merged.append(self._merge_line(current_line))
        
        return merged
    
    def _merge_line(self, regions: List[TextRegion]) -> TextRegion:
        """合并一行的文字区域"""
        if len(regions) == 1:
            return regions[0]
        
        # 按x坐标排序
        sorted_regions = sorted(regions, key=lambda r: r.bbox[0])
        
        # 合并文本
        text = " ".join(r.text for r in sorted_regions)
        
        # 计算合并后的bbox
        x = min(r.bbox[0] for r in sorted_regions)
        y = min(r.bbox[1] for r in sorted_regions)
        x2 = max(r.bbox[0] + r.bbox[2] for r in sorted_regions)
        y2 = max(r.bbox[1] + r.bbox[3] for r in sorted_regions)
        
        # 平均置信度
        avg_conf = sum(r.confidence for r in sorted_regions) / len(sorted_regions)
        
        return TextRegion(
            text=text,
            bbox=(x, y, x2 - x, y2 - y),
            confidence=avg_conf
        )
    
    def replace_text_in_image(
        self,
        image_data: bytes,
        text_replacements: List[Tuple[TextRegion, str]],
        target_lang: str = "zh-CN"
    ) -> bytes:
        """
        在图片中替换文字
        
        Args:
            image_data: 原图片数据
            text_replacements: [(原文字区域, 翻译文本), ...]
            target_lang: 目标语言
        
        Returns:
            处理后的图片数据
        """
        # 打开图片
        image = Image.open(io.BytesIO(image_data))
        
        # 转换为RGB模式（如果需要）
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        draw = ImageDraw.Draw(image)
        
        # 获取合适的字体
        font = self._get_font(target_lang)
        
        for region, translated_text in text_replacements:
            x, y, w, h = region.bbox
            
            # 用背景色填充原文字区域
            bg_color = self._detect_background_color(image, (x, y, x + w, y + h))
            draw.rectangle([x, y, x + w, y + h], fill=bg_color)
            
            # 计算合适的字体大小
            font_size = self._calculate_font_size(translated_text, w, h, font)
            try:
                sized_font = ImageFont.truetype(font.path, font_size)
            except Exception:
                sized_font = font
            
            # 获取文字颜色（使用原文字的主要颜色）
            text_color = self._detect_text_color(image, (x, y, x + w, y + h), bg_color)
            
            # 绘制翻译文字
            # 计算文字位置以居中显示
            text_bbox = draw.textbbox((0, 0), translated_text, font=sized_font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            
            text_x = x + (w - text_width) / 2
            text_y = y + (h - text_height) / 2
            
            draw.text((text_x, text_y), translated_text, fill=text_color, font=sized_font)
        
        # 保存为bytes
        output = io.BytesIO()
        image.save(output, format='PNG')
        return output.getvalue()
    
    def _get_font(self, lang: str, size: int = 12) -> ImageFont.FreeTypeFont:
        """获取合适的字体"""
        # 根据目标语言选择字体
        font_paths = {
            "zh-CN": [
                "/System/Library/Fonts/PingFang.ttc",
                "/System/Library/Fonts/STHeiti Light.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                "C:/Windows/Fonts/msyh.ttc",
            ],
            "zh-TW": [
                "/System/Library/Fonts/PingFang.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            ],
            "ja": [
                "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            ],
        }
        
        paths = font_paths.get(lang, [])
        
        for path in paths:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        
        # 使用默认字体
        try:
            return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
        except Exception:
            return ImageFont.load_default()
    
    def _calculate_font_size(
        self, 
        text: str, 
        width: int, 
        height: int,
        font: ImageFont.FreeTypeFont
    ) -> int:
        """计算合适的字体大小"""
        # 从较大的字号开始尝试
        for size in range(24, 6, -1):
            try:
                test_font = ImageFont.truetype(font.path, size)
                bbox = test_font.getbbox(text)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                
                if text_width <= width * 0.95 and text_height <= height * 0.95:
                    return size
            except Exception:
                continue
        
        return 8  # 最小字号
    
    def _detect_background_color(
        self, 
        image: Image.Image, 
        bbox: Tuple[int, int, int, int]
    ) -> Tuple[int, int, int]:
        """检测区域的背景颜色"""
        x1, y1, x2, y2 = bbox
        
        # 确保坐标在图片范围内
        x1 = max(0, int(x1))
        y1 = max(0, int(y1))
        x2 = min(image.width, int(x2))
        y2 = min(image.height, int(y2))
        
        if x2 <= x1 or y2 <= y1:
            return (255, 255, 255)  # 默认白色
        
        # 裁剪区域
        region = image.crop((x1, y1, x2, y2))
        
        # 获取边缘像素的颜色（假设边缘是背景）
        pixels = []
        width, height = region.size
        
        # 采样边缘像素
        for x in range(width):
            pixels.append(region.getpixel((x, 0)))
            pixels.append(region.getpixel((x, height - 1)))
        for y in range(height):
            pixels.append(region.getpixel((0, y)))
            pixels.append(region.getpixel((width - 1, y)))
        
        # 计算平均颜色
        if not pixels:
            return (255, 255, 255)
        
        r = sum(p[0] if isinstance(p, tuple) else p for p in pixels) // len(pixels)
        g = sum(p[1] if isinstance(p, tuple) and len(p) > 1 else p for p in pixels) // len(pixels)
        b = sum(p[2] if isinstance(p, tuple) and len(p) > 2 else p for p in pixels) // len(pixels)
        
        return (r, g, b)
    
    def _detect_text_color(
        self,
        image: Image.Image,
        bbox: Tuple[int, int, int, int],
        bg_color: Tuple[int, int, int]
    ) -> Tuple[int, int, int]:
        """检测文字颜色（与背景对比度最大的颜色）"""
        # 简单策略：如果背景亮，用深色文字；反之用浅色文字
        brightness = (bg_color[0] + bg_color[1] + bg_color[2]) / 3
        
        if brightness > 128:
            return (0, 0, 0)  # 黑色文字
        else:
            return (255, 255, 255)  # 白色文字
