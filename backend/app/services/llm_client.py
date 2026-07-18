"""
LLM客户端 - 支持OpenAI API和本地CLI调用
"""
import asyncio
import json
import shutil
import re
import os
import html
from pathlib import Path
from typing import Optional, List, Dict, Any
from abc import ABC, abstractmethod
import httpx

from app.models.schemas import LLMProvider


def check_cli_exists(command: str) -> bool:
    """检查CLI命令是否存在"""
    return shutil.which(command) is not None


class BaseLLMClient(ABC):
    """LLM客户端基类"""
    
    @abstractmethod
    async def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: Optional[str] = None,
        abbreviations: Optional[Dict[str, str]] = None
    ) -> str:
        """翻译文本"""
        pass

    async def translate_segments(
        self,
        segments: List[Dict[str, str]],
        source_lang: str,
        target_lang: str,
        abbreviations: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """
        批量翻译带稳定 ID 的文本片段。

        PDF 中的目录项、表格单元格和注释往往会被模型合并或重新分段。
        这里用不可翻译的 ID 建立映射；若模型遗漏了某个 ID，则只对遗漏
        片段进行单独重试，绝不使用段落位置猜测映射关系。
        """
        if not segments:
            return {}

        expected = {
            str(segment["id"]): str(segment.get("text", ""))
            for segment in segments
            if segment.get("id")
        }
        payload = "\n".join(
            f'<dta-segment id="{html.escape(segment_id, quote=True)}">'
            f"{html.escape(text, quote=False)}</dta-segment>"
            for segment_id, text in expected.items()
        )
        context = (
            "这是 PDF 布局片段批量翻译。必须逐个翻译每个 <dta-segment> "
            "的正文，原样保留其 id、开始标签、结束标签和片段顺序；"
            "不得合并、拆分、遗漏片段；尽量保留片段内部换行；"
            "URL、编号、公式、代码和专有模型名保持原样；"
            "不要输出标签之外的内容。"
        )

        raw_result = await self.translate(
            payload,
            source_lang,
            target_lang,
            context=context,
            abbreviations=abbreviations
        )
        translated = self._parse_segment_response(raw_result, set(expected))

        # 模型偶尔会漏掉标记。逐项补译比把剩余译文按换行猜回去安全得多。
        for segment_id, source_text in expected.items():
            if translated.get(segment_id, "").strip():
                continue
            translated[segment_id] = (
                await self.translate(
                    source_text,
                    source_lang,
                    target_lang,
                    context="只翻译这个独立的 PDF 文本片段；不要添加解释。",
                    abbreviations=abbreviations
                )
            ).strip()

        return translated

    @staticmethod
    def _parse_segment_response(
        response: str,
        expected_ids: set[str]
    ) -> Dict[str, str]:
        """从模型响应中提取稳定 ID；忽略任何意外或重复的 ID。"""
        if not response:
            return {}

        pattern = re.compile(
            r'<dta-segment\s+id=["\']([^"\']+)["\']\s*>(.*?)</dta-segment>',
            re.IGNORECASE | re.DOTALL
        )
        translated: Dict[str, str] = {}
        for segment_id, text in pattern.findall(response):
            segment_id = html.unescape(segment_id.strip())
            if segment_id in expected_ids and segment_id not in translated:
                translated[segment_id] = html.unescape(text.strip())
        return translated
    
    @abstractmethod
    async def detect_abbreviations(
        self,
        text: str,
        target_lang: str
    ) -> List[Dict[str, str]]:
        """检测并解释缩写"""
        pass


class OpenAIClient(BaseLLMClient):
    """OpenAI兼容API客户端"""
    
    def __init__(
        self,
        api_key: str,
        api_base: Optional[str] = None,
        model: str = "gpt-4"
    ):
        self.api_key = api_key
        self.api_base = api_base or "https://api.openai.com/v1"
        self.model = model
        self.client = httpx.AsyncClient(timeout=120.0)
    
    async def _chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3
    ) -> str:
        """调用Chat Completion API"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature
        }
        
        response = await self.client.post(
            f"{self.api_base}/chat/completions",
            headers=headers,
            json=data
        )
        response.raise_for_status()
        
        result = response.json()
        return result["choices"][0]["message"]["content"]
    
    async def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: Optional[str] = None,
        abbreviations: Optional[Dict[str, str]] = None
    ) -> str:
        """翻译文本"""
        system_prompt = f"""你是一位专业的文档翻译专家。请将以下{source_lang}文本翻译成{target_lang}。

翻译要求：
1. 保持原文的段落结构和格式
2. 专业术语要准确翻译
3. 保持学术/专业文档的语言风格
4. 如果遇到缩写，第一次出现时给出全称和翻译，格式为：缩写（全称，翻译）
5. URL、电子邮箱、DOI、arXiv编号、引用编号、公式、数值、代码和模型名必须保持原样
6. 目录项、表格单元格、图注、脚注和页眉页脚也要完整翻译
7. 不要添加额外的解释或注释，只输出翻译结果"""

        if abbreviations:
            abbr_info = "\n".join([f"- {k}: {v}" for k, v in abbreviations.items()])
            system_prompt += f"\n\n已知缩写列表（这些缩写已经解释过，直接使用缩写即可）：\n{abbr_info}"
        
        user_message = text
        if context:
            user_message = f"上下文信息：{context}\n\n待翻译文本：\n{text}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        return await self._chat_completion(messages)
    
    async def detect_abbreviations(
        self,
        text: str,
        target_lang: str
    ) -> List[Dict[str, str]]:
        """检测文本中的缩写"""
        system_prompt = f"""分析以下文本，找出所有的缩写词（如 API, PDF, NLP 等）。
对于每个缩写，提供：
1. 缩写本身
2. 英文全称
3. {target_lang}翻译

请以JSON数组格式返回，格式如下：
[{{"abbreviation": "API", "full_form": "Application Programming Interface", "translation": "应用程序编程接口"}}]

如果没有找到缩写，返回空数组 []"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ]
        
        result = await self._chat_completion(messages, temperature=0.1)
        
        # 解析JSON结果
        try:
            # 尝试提取JSON部分
            json_match = re.search(r'\[.*\]', result, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return []
        except json.JSONDecodeError:
            return []
    
    async def close(self):
        """关闭客户端"""
        await self.client.aclose()


class ClaudeCLIClient(BaseLLMClient):
    """Claude CLI客户端"""
    
    def __init__(self):
        self.cli_command = "claude"
        # 检查CLI是否存在
        if not check_cli_exists(self.cli_command):
            raise ValueError(
                f"未找到 '{self.cli_command}' 命令。\n"
                f"请确保已安装 Claude CLI 并添加到系统 PATH 中。\n"
                f"安装方法：npm install -g @anthropic-ai/claude-cli"
            )
    
    async def _run_cli(self, prompt: str) -> str:
        """运行Claude CLI命令"""
        try:
            process = await asyncio.create_subprocess_exec(
                self.cli_command,
                "-p", prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                raise RuntimeError(f"Claude CLI error: {stderr.decode()}")
            
            return stdout.decode().strip()
        except FileNotFoundError:
            raise RuntimeError(
                f"无法执行 '{self.cli_command}' 命令，请确保 Claude CLI 已正确安装"
            )
    
    async def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: Optional[str] = None,
        abbreviations: Optional[Dict[str, str]] = None
    ) -> str:
        """翻译文本"""
        prompt = f"""你是一位专业的文档翻译专家。请将以下{source_lang}文本翻译成{target_lang}。

翻译要求：
1. 保持原文的段落结构和格式
2. 专业术语要准确翻译
3. 保持学术/专业文档的语言风格
4. 如果遇到缩写，第一次出现时给出全称和翻译，格式为：缩写（全称，翻译）
5. URL、电子邮箱、DOI、arXiv编号、引用编号、公式、数值、代码和模型名必须保持原样
6. 目录项、表格单元格、图注、脚注和页眉页脚也要完整翻译
7. 不要添加额外的解释或注释，只输出翻译结果"""

        if abbreviations:
            abbr_info = "\n".join([f"- {k}: {v}" for k, v in abbreviations.items()])
            prompt += f"\n\n已知缩写列表（这些缩写已经解释过，直接使用缩写即可）：\n{abbr_info}"
        
        if context:
            prompt += f"\n\n上下文信息：{context}"
        
        prompt += f"\n\n待翻译文本：\n{text}"
        
        return await self._run_cli(prompt)
    
    async def detect_abbreviations(
        self,
        text: str,
        target_lang: str
    ) -> List[Dict[str, str]]:
        """检测文本中的缩写"""
        prompt = f"""分析以下文本，找出所有的缩写词（如 API, PDF, NLP 等）。
对于每个缩写，提供：
1. 缩写本身
2. 英文全称
3. {target_lang}翻译

请以JSON数组格式返回，格式如下：
[{{"abbreviation": "API", "full_form": "Application Programming Interface", "translation": "应用程序编程接口"}}]

如果没有找到缩写，返回空数组 []

文本：
{text}"""
        
        result = await self._run_cli(prompt)
        
        try:
            json_match = re.search(r'\[.*\]', result, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return []
        except json.JSONDecodeError:
            return []


class CodexCLIClient(BaseLLMClient):
    """Codex CLI客户端（集成在ChatGPT桌面应用中）"""
    
    def __init__(self, workdir: Optional[str] = None):
        self.cli_command = self._find_codex_executable()
        self.workdir = workdir or "/tmp/codex_translation"
    
    def _find_codex_executable(self) -> str:
        """查找Codex可执行文件"""
        
        # 优先使用环境变量
        env_path = os.getenv("CODEX_CLI_PATH", "")
        if env_path and (Path(env_path).is_file() or shutil.which(env_path)):
            return env_path
        
        # 尝试常见路径（Codex现已集成到ChatGPT桌面应用中）
        candidates = [
            "codex",  # PATH中
            "/Applications/ChatGPT.app/Contents/Resources/codex",  # macOS ChatGPT桌面应用
            "/Applications/Codex.app/Contents/Resources/codex",   # 旧版独立应用（已废弃）
            "/usr/local/bin/codex",
            "/opt/homebrew/bin/codex",
        ]
        
        for candidate in candidates:
            if Path(candidate).is_file() or shutil.which(candidate):
                return candidate
        
        raise ValueError(
            "未找到 Codex CLI。\n"
            "Codex 现已集成在 ChatGPT 桌面应用中，请确保已安装 ChatGPT.app。\n"
            "常见路径：/Applications/ChatGPT.app/Contents/Resources/codex\n"
            "或设置环境变量 CODEX_CLI_PATH 指向 codex 可执行文件。"
        )
    
    async def _run_cli(self, prompt: str) -> str:
        """运行Codex CLI命令"""
        # 确保工作目录存在
        workdir = Path(self.workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        
        # 构建命令参数
        # 参考: codex exec --help
        command = [
            self.cli_command,
            "exec",
            "--cd", str(workdir),
            "--sandbox", "workspace-write",
            "--skip-git-repo-check",
            "--color", "never",
            "--json",
            "--ephemeral",  # 不保存session文件
            "-"  # 从stdin读取prompt
        ]
        
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workdir)
            )
            
            stdout, stderr = await process.communicate(input=prompt.encode('utf-8'))
            
            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                raise RuntimeError(f"Codex CLI error: {error_msg}")
            
            # 解析JSON输出，提取agent_message内容
            output = stdout.decode().strip()
            return self._parse_codex_output(output)
            
        except FileNotFoundError:
            raise RuntimeError(
                f"无法执行 Codex CLI，请确保 ChatGPT.app 已正确安装"
            )
    
    def _parse_codex_output(self, output: str) -> str:
        """解析Codex JSON输出"""
        result_parts = []
        
        for line in output.split('\n'):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                # 提取agent_message类型的completed事件
                item = event.get("item", {})
                event_type = event.get("type", "")
                
                if item.get("type") == "agent_message" and event_type == "item.completed":
                    text = item.get("text", "")
                    if text:
                        result_parts.append(text)
                elif event_type == "error":
                    raise RuntimeError(event.get("message", "Codex 执行出错"))
            except json.JSONDecodeError:
                # 非JSON行，可能是普通输出
                if line and not line.startswith('{'):
                    result_parts.append(line)
        
        return "\n".join(result_parts) if result_parts else output
    
    async def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: Optional[str] = None,
        abbreviations: Optional[Dict[str, str]] = None
    ) -> str:
        """翻译文本"""
        prompt = f"""你是一位专业的文档翻译专家。请将以下{source_lang}文本翻译成{target_lang}。

翻译要求：
1. 保持原文的段落结构和格式
2. 专业术语要准确翻译
3. 保持学术/专业文档的语言风格
4. 如果遇到缩写，第一次出现时给出全称和翻译，格式为：缩写（全称，翻译）
5. URL、电子邮箱、DOI、arXiv编号、引用编号、公式、数值、代码和模型名必须保持原样
6. 目录项、表格单元格、图注、脚注和页眉页脚也要完整翻译
7. 不要添加额外的解释或注释，只输出翻译结果"""

        if abbreviations:
            abbr_info = "\n".join([f"- {k}: {v}" for k, v in abbreviations.items()])
            prompt += f"\n\n已知缩写列表（这些缩写已经解释过，直接使用缩写即可）：\n{abbr_info}"
        
        if context:
            prompt += f"\n\n上下文信息：{context}"
        
        prompt += f"\n\n待翻译文本：\n{text}"
        
        return await self._run_cli(prompt)
    
    async def detect_abbreviations(
        self,
        text: str,
        target_lang: str
    ) -> List[Dict[str, str]]:
        """检测文本中的缩写"""
        prompt = f"""分析以下文本，找出所有的缩写词（如 API, PDF, NLP 等）。
对于每个缩写，提供：
1. 缩写本身
2. 英文全称
3. {target_lang}翻译

请以JSON数组格式返回，格式如下：
[{{"abbreviation": "API", "full_form": "Application Programming Interface", "translation": "应用程序编程接口"}}]

如果没有找到缩写，返回空数组 []

文本：
{text}"""
        
        result = await self._run_cli(prompt)
        
        try:
            json_match = re.search(r'\[.*\]', result, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return []
        except json.JSONDecodeError:
            return []


def create_llm_client(
    provider: LLMProvider,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    model: Optional[str] = None
) -> BaseLLMClient:
    """创建LLM客户端工厂函数"""
    if provider == LLMProvider.OPENAI:
        if not api_key:
            raise ValueError("OpenAI API需要提供API密钥")
        return OpenAIClient(
            api_key=api_key,
            api_base=api_base,
            model=model or "gpt-4"
        )
    elif provider == LLMProvider.CLAUDE_CLI:
        return ClaudeCLIClient()
    elif provider == LLMProvider.CODEX_CLI:
        return CodexCLIClient()
    else:
        raise ValueError(f"不支持的LLM提供者: {provider}")
