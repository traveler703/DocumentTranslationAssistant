# DocumentTranslationAssistant

文档翻译助手 - 智能PDF翻译工具，保留原文排版

## 功能特性

- **多语言支持**：支持英文、法文、西班牙文、德文、简体中文、正体中文、日文互译
- **智能布局识别**：自动识别分栏、段落分页，准确提取文本内容
- **图表翻译**：自动识别并翻译图片中的文字，生成新图片替换原图
- **缩写处理**：首次出现的缩写自动展示全称和翻译
- **保留排版**：翻译后的PDF尽可能保持原文档的格式和布局
- **多种翻译引擎**：支持OpenAI API、本地Claude CLI、Codex CLI

## 技术栈

### 后端
- **框架**：FastAPI
- **PDF处理**：PyMuPDF (fitz)
- **图片处理**：Pillow + pytesseract (OCR)
- **异步支持**：uvicorn + aiofiles

### 前端
- **框架**：React 18 + TypeScript
- **构建工具**：Vite
- **样式**：Tailwind CSS
- **图标**：Lucide React

## 快速开始

### 环境要求

- Python 3.9+
- Node.js 18+
- Tesseract OCR（用于图片文字识别）

### 安装 Tesseract

```bash
# macOS
brew install tesseract tesseract-lang

# Ubuntu/Debian
sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-jpn

# Windows
# 从 https://github.com/UB-Mannheim/tesseract/wiki 下载安装
```

### 后端安装

```bash
cd backend

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 复制环境变量配置
cp .env.example .env

# 启动服务
python run.py
```

后端服务将在 http://localhost:8000 启动，API文档在 http://localhost:8000/api/docs

### 前端安装

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

前端服务将在 http://localhost:3000 启动

## 使用方法

1. **上传文档**：拖拽PDF文件到上传区域，或点击选择文件
2. **配置翻译**：
   - 选择源语言和目标语言
   - 选择翻译引擎（OpenAI API / Claude CLI / Codex CLI）
   - 如使用OpenAI API，输入API Key
3. **开始翻译**：点击"开始翻译"按钮
4. **下载结果**：翻译完成后，点击下载按钮获取翻译后的PDF

## 项目结构

```
DocumentTranslationAssistant/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI应用入口
│   │   ├── config.py            # 配置管理
│   │   ├── routers/             # API路由
│   │   │   ├── upload.py        # 文件上传
│   │   │   ├── translation.py   # 翻译API
│   │   │   └── user.py          # 用户API（预留）
│   │   ├── services/            # 业务服务
│   │   │   ├── pdf_processor.py # PDF处理
│   │   │   ├── translator.py    # 翻译服务
│   │   │   ├── llm_client.py    # LLM客户端
│   │   │   └── image_processor.py # 图片处理
│   │   ├── models/              # 数据模型
│   │   └── utils/               # 工具函数
│   ├── requirements.txt
│   └── run.py
├── frontend/
│   ├── src/
│   │   ├── components/          # React组件
│   │   ├── services/            # API服务
│   │   ├── types/               # TypeScript类型
│   │   └── App.tsx              # 主应用
│   ├── package.json
│   └── vite.config.ts
└── README.md
```

## API 文档

启动后端后，访问以下地址查看API文档：
- Swagger UI: http://localhost:8000/api/docs
- ReDoc: http://localhost:8000/api/redoc

### 主要API

| 方法 | 路径 | 描述 |
|------|------|------|
| POST | /api/files/upload | 上传PDF文件 |
| GET | /api/files/download/{file_id} | 下载翻译结果 |
| POST | /api/translation/start | 开始翻译任务 |
| GET | /api/translation/progress/{task_id} | 获取翻译进度 |
| GET | /api/translation/result/{task_id} | 获取翻译结果 |

## 配置说明

### 环境变量 (.env)

```bash
# LLM配置
LLM_PROVIDER=openai          # openai / claude_cli / codex_cli
OPENAI_API_KEY=your-key      # OpenAI API密钥
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_MODEL=gpt-4

# CLI路径配置（可选，如果CLI不在PATH中）
CLAUDE_CLI_PATH=/usr/local/bin/claude
CODEX_CLI_PATH=/Applications/Codex.app/Contents/Resources/codex

# 默认语言
DEFAULT_SOURCE_LANG=en
DEFAULT_TARGET_LANG=zh-CN

# 服务器配置
HOST=0.0.0.0
PORT=8000
DEBUG=true
```

### 翻译引擎配置

#### OpenAI API
直接在界面中输入API Key即可使用。支持任何兼容OpenAI API的服务。

#### Claude CLI
需要先安装Claude CLI：
```bash
# 确保claude命令在PATH中
which claude

# 如果不在PATH中，设置环境变量
export CLAUDE_CLI_PATH=/path/to/claude
```

#### Codex CLI
Codex 现已集成在 ChatGPT 桌面应用中：
1. 下载并安装 [ChatGPT 桌面应用](https://openai.com/chatgpt/desktop)
2. Codex CLI 会自动从 ChatGPT.app 中发现，通常无需额外配置
3. 如需手动指定路径：
```bash
# macOS - ChatGPT桌面应用中的Codex
export CODEX_CLI_PATH=/Applications/ChatGPT.app/Contents/Resources/codex

# 或添加到PATH
export PATH="$PATH:/Applications/ChatGPT.app/Contents/Resources"
```

## 性能优化

本项目实现了多项性能优化，以减少翻译时间和token消耗：

### 并行翻译
- 文档按批次划分，最多 **5个批次并行翻译**
- 每批最多包含5页或8000字符
- 图片文字也支持并行处理

### Token优化
- **缩写检测**：只在前10页（或总页数的20%）中检测，后续页面复用结果
- **批量合并**：多页文本合并为一次API调用，减少重复的system prompt
- **图片文字批量翻译**：同一图片中的多段文字合并翻译

### 预计效果
- 70页PDF：从约20分钟降至约5-8分钟（取决于网络和API响应）
- Token使用：减少约40-60%

## 预留接口

以下功能已预留接口，可在后续版本中实现：
- 用户注册/登录
- 翻译历史记录
- 任务取消

## 开发计划

- [ ] 用户认证系统
- [ ] 翻译历史管理
- [ ] 批量文件翻译
- [ ] 更多PDF布局支持
- [ ] 翻译质量优化

## 许可证

MIT License