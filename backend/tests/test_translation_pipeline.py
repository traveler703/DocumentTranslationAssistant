import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from app.services.image_processor import ImageProcessor
from app.services.llm_client import BaseLLMClient
from app.services.pdf_processor import (
    PDFGenerator,
    PageLayout,
    TextBlock,
    TextLine,
    TextSpan,
)
from app.services.translator import TranslationService, TranslationTask
from app.models.schemas import TranslationStatus


def make_block(text: str, page_num: int, block_type: str = "text") -> TextBlock:
    span = TextSpan(
        text=text,
        bbox=(40, 40, 300, 58),
        font_name="Helvetica",
        font_size=10,
        color=0,
        flags=0,
    )
    line = TextLine(spans=[span], bbox=span.bbox)
    return TextBlock(
        lines=[line],
        bbox=span.bbox,
        block_type=block_type,
        page_num=page_num,
    )


class MarkerDroppingClient(BaseLLMClient):
    """模拟批量响应漏掉第二个 ID，验证逐项补译逻辑。"""

    def __init__(self):
        self.calls = []

    async def translate(
        self,
        text,
        source_lang,
        target_lang,
        context=None,
        abbreviations=None,
    ):
        self.calls.append(text)
        if "<dta-segment" in text:
            first_id = text.split('id="', 1)[1].split('"', 1)[0]
            return f'<dta-segment id="{first_id}">批量译文</dta-segment>'
        return f"补译：{text}"

    async def detect_abbreviations(self, text, target_lang):
        return []


class ConciseLabelClient(MarkerDroppingClient):
    async def translate_segments(
        self, segments, source_lang, target_lang, abbreviations=None
    ):
        return {
            segment["id"]: "BBox（Bounding Box，边界框）"
            for segment in segments
        }

    async def translate(
        self,
        text,
        source_lang,
        target_lang,
        context=None,
        abbreviations=None,
    ):
        self.calls.append((text, context))
        return "边界框"


class IdentityClient(MarkerDroppingClient):
    async def translate_segments(
        self, segments, source_lang, target_lang, abbreviations=None
    ):
        return {segment["id"]: segment["text"] for segment in segments}


class ExplodingClient(MarkerDroppingClient):
    async def detect_abbreviations(self, text, target_lang):
        raise RuntimeError("simulated background failure")


class TranslationMappingTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_segment_is_retried_without_positional_guessing(self):
        client = MarkerDroppingClient()
        result = await client.translate_segments(
            [
                {"id": "p1-b1", "text": "Table of Contents"},
                {"id": "p1-b2", "text": "Figure note"},
            ],
            "英文",
            "简体中文",
        )

        self.assertEqual(result["p1-b1"], "批量译文")
        self.assertEqual(result["p1-b2"], "补译：Figure note")
        self.assertEqual(len(client.calls), 2)

    async def test_headers_footers_and_table_blocks_are_all_mapped(self):
        layout = PageLayout(
            page_num=0,
            width=595,
            height=842,
            text_blocks=[
                make_block("Repeated running header", 0, "header"),
                make_block("Table of Contents", 0),
                make_block("Figure 1 note", 0),
                make_block("Copyright notice", 0, "footer"),
            ],
        )
        service = TranslationService()
        task = TranslationTask(
            task_id="task",
            file_id="file",
            source_path=Path("source.pdf"),
            source_lang="en",
            target_lang="zh-CN",
            total_pages=1,
            skip_references=False,
            skip_appendix=False,
        )
        batches = service._create_translation_batches([layout], task)

        self.assertEqual(len(batches), 1)
        self.assertEqual(
            [item.unit_id for item in batches[0].items],
            ["p1-b1", "p1-b2", "p1-b3", "p1-b4"],
        )

        client = MarkerDroppingClient()
        translated = await service._translate_single_batch(
            batches[0], client, task
        )
        self.assertEqual(len(translated[0]), 4)

    async def test_directory_columns_are_split_and_identifiers_are_preserved(self):
        number = TextLine(
            spans=[make_block("1", 0).lines[0].spans[0]],
            bbox=(40, 40, 45, 52),
        )
        title = TextLine(
            spans=[make_block("Introduction", 0).lines[0].spans[0]],
            bbox=(60, 40, 130, 52),
        )
        page_number = TextLine(
            spans=[make_block("5", 0).lines[0].spans[0]],
            bbox=(260, 40, 265, 52),
        )
        toc_block = TextBlock(
            lines=[number, title, page_number],
            bbox=(40, 40, 265, 52),
            page_num=0,
        )

        parts = TranslationService._split_fragmented_block(toc_block)
        self.assertEqual(len(parts), 3)
        self.assertFalse(TranslationService._should_translate("1"))
        self.assertTrue(TranslationService._should_translate("Introduction"))
        self.assertFalse(
            TranslationService._should_translate(
                "arXiv:2507.01925v1 [cs.RO] 2 Jul 2025"
            )
        )

    async def test_tiny_rotated_label_is_retranslated_concisely(self):
        span = TextSpan(
            text="BBox",
            bbox=(79.29, 340.03, 89.58, 361.72),
            font_name="Times",
            font_size=9.29,
            color=0,
            flags=0,
        )
        line = TextLine(
            spans=[span],
            bbox=span.bbox,
            direction=(0.0, -1.0),
        )
        block = TextBlock(
            lines=[line],
            bbox=span.bbox,
            page_num=0,
        )
        layout = PageLayout(
            page_num=0,
            width=595,
            height=842,
            text_blocks=[block],
        )
        service = TranslationService()
        task = TranslationTask(
            task_id="task",
            file_id="file",
            source_path=Path("source.pdf"),
            source_lang="en",
            target_lang="zh-CN",
            total_pages=1,
            skip_references=False,
            skip_appendix=False,
        )
        batch = service._create_translation_batches([layout], task)[0]
        client = ConciseLabelClient()

        translated = await service._translate_single_batch(
            batch, client, task
        )

        self.assertEqual(translated[0][0][1], "边界框")
        self.assertEqual(service._estimate_compact_char_limit(block), 4)
        self.assertIn("最多 4 个", client.calls[0][1])

    async def test_unchanged_formula_keeps_original_pdf_font(self):
        layout = PageLayout(
            page_num=0,
            width=300,
            height=200,
            text_blocks=[make_block("𝜋0-FAST", 0)],
        )
        service = TranslationService()
        task = TranslationTask(
            task_id="task",
            file_id="file",
            source_path=Path("source.pdf"),
            source_lang="en",
            target_lang="zh-CN",
            total_pages=1,
            skip_references=False,
            skip_appendix=False,
        )
        batch = service._create_translation_batches([layout], task)[0]

        translated = await service._translate_single_batch(
            batch, IdentityClient(), task
        )

        self.assertEqual(translated[0], [])


class ImageProcessorTests(unittest.TestCase):
    def test_missing_tesseract_is_optional_and_does_not_raise(self):
        processor = ImageProcessor(
            tesseract_cmd="/definitely/missing/tesseract"
        )
        self.assertFalse(processor.ocr_available)
        self.assertEqual(processor.extract_text_from_image(b""), [])


class TranslationServiceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_ocr_still_completes_pdf_task(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.pdf"
            output_path = Path(temp_dir) / "translated.pdf"
            doc = fitz.open()
            page = doc.new_page(width=300, height=200)
            page.insert_text((40, 55), "Document title", fontsize=12)
            pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), 0)
            pixmap.clear_with(255)
            page.insert_image((40, 80, 140, 130), pixmap=pixmap)
            doc.save(source_path)
            doc.close()

            service = TranslationService()
            service.image_processor = ImageProcessor(
                tesseract_cmd="/definitely/missing/tesseract"
            )
            task = TranslationTask(
                task_id="task",
                file_id="file",
                source_path=source_path,
                output_path=output_path,
                source_lang="en",
                target_lang="zh-CN",
                total_pages=1,
            )

            await service.execute_translation(task, MarkerDroppingClient())

            self.assertEqual(task.status, TranslationStatus.COMPLETED)
            self.assertTrue(output_path.exists())
            self.assertIn("Tesseract", task.message)

    async def test_background_failure_is_recorded_without_escaping_asgi(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.pdf"
            doc = fitz.open()
            page = doc.new_page(width=300, height=200)
            page.insert_text((40, 55), "Document title", fontsize=12)
            doc.save(source_path)
            doc.close()

            service = TranslationService()
            task = TranslationTask(
                task_id="task",
                file_id="file",
                source_path=source_path,
                output_path=Path(temp_dir) / "translated.pdf",
                source_lang="en",
                target_lang="zh-CN",
                total_pages=1,
            )

            with patch("app.services.translator.logger.exception"):
                await service.execute_translation(task, ExplodingClient())

            self.assertEqual(task.status, TranslationStatus.FAILED)
            self.assertEqual(task.error, "simulated background failure")


class PDFGeneratorTests(unittest.TestCase):
    def test_unicode_font_is_embedded_and_original_text_is_removed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.pdf"
            output_path = Path(temp_dir) / "translated.pdf"

            doc = fitz.open()
            page = doc.new_page(width=300, height=200)
            page.draw_line((30, 80), (270, 80), color=(0, 0, 0))
            page.insert_text((40, 55), "Table of Contents", fontsize=12)
            source_bbox = page.search_for("Table of Contents")[0]
            page.insert_link({
                "kind": fitz.LINK_URI,
                "from": source_bbox,
                "uri": "https://example.com/contents",
            })
            doc.save(source_path)
            doc.close()

            source_doc = fitz.open(source_path)
            bbox = tuple(source_doc[0].search_for("Table of Contents")[0])
            source_doc.close()
            style = {
                "font_size": 12,
                "color": (0, 0, 0),
                "redaction_bboxes": [bbox],
            }

            with PDFGenerator(str(source_path)) as generator:
                generator.create_translated_pdf(
                    {0: [(bbox, "目录", style)]},
                    str(output_path),
                    target_lang="zh-CN",
                )

            translated_doc = fitz.open(output_path)
            translated_text = translated_doc[0].get_text()
            drawings = translated_doc[0].get_drawings()
            links = translated_doc[0].get_links()
            translated_doc.close()

            self.assertIn("目录", translated_text)
            self.assertNotIn("Table of Contents", translated_text)
            self.assertGreaterEqual(len(drawings), 1)
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0]["uri"], "https://example.com/contents")

    def test_unplaceable_single_label_does_not_abort_document(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.pdf"
            output_path = Path(temp_dir) / "translated.pdf"
            doc = fitz.open()
            doc.new_page(width=100, height=100)
            doc.save(source_path)
            doc.close()

            bbox = (20, 20, 30, 42)
            style = {
                "font_size": 9,
                "color": (0, 0, 0),
                "redaction_bboxes": [bbox],
                "rotation": 90,
                "lineheight": 1.0,
            }
            with PDFGenerator(str(source_path)) as generator:
                generator.create_translated_pdf(
                    {0: [(bbox, "这是一个无法放入的超长图表标签", style)]},
                    str(output_path),
                    target_lang="zh-CN",
                )
                warnings = generator.layout_warnings

            self.assertTrue(output_path.exists())
            self.assertEqual(len(warnings), 1)

    def test_real_page_preflight_keeps_original_when_initial_fit_is_wrong(self):
        """空白页误判能放入时，真实页面预演仍须在 redaction 前拦截。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.pdf"
            output_path = Path(temp_dir) / "translated.pdf"
            doc = fitz.open()
            page = doc.new_page(width=240, height=120)
            page.insert_text((30, 52), "Original label", fontsize=10)
            source_bbox = tuple(page.search_for("Original label")[0])
            doc.save(source_path)
            doc.close()

            style = {
                "font_size": 10,
                "color": (0, 0, 0),
                "redaction_bboxes": [source_bbox],
                "rotation": 0,
                "lineheight": 1.0,
            }
            with patch.object(
                PDFGenerator, "_fit_font_size", return_value=40.0
            ):
                with PDFGenerator(str(source_path)) as generator:
                    generator.create_translated_pdf(
                        {
                            0: [(
                                source_bbox,
                                "这段译文在真实页面中肯定无法放入",
                                style,
                            )]
                        },
                        str(output_path),
                        target_lang="zh-CN",
                    )
                    warnings = generator.layout_warnings

            translated_doc = fitz.open(output_path)
            translated_text = translated_doc[0].get_text()
            translated_doc.close()

            self.assertIn("Original label", translated_text)
            self.assertNotIn("这段译文", translated_text)
            self.assertTrue(
                any("真实页面预演" in warning for warning in warnings)
            )


if __name__ == "__main__":
    unittest.main()
