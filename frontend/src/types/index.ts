/**
 * API 服务类型定义
 */

// 语言代码
export type LanguageCode = 'en' | 'fr' | 'es' | 'de' | 'zh-CN' | 'zh-TW' | 'ja';

// LLM 提供者
export type LLMProvider = 'openai' | 'claude_cli' | 'codex_cli';

// 翻译状态
export type TranslationStatus = 'pending' | 'processing' | 'completed' | 'failed';

// 语言信息
export interface LanguageInfo {
  code: LanguageCode;
  name: string;
}

// LLM 提供者信息
export interface LLMProviderInfo {
  id: LLMProvider;
  name: string;
  description: string;
  requires_api_key: boolean;
}

// 文件上传响应
export interface FileUploadResponse {
  file_id: string;
  filename: string;
  size: number;
  page_count: number;
  message: string;
}

// 翻译请求
export interface TranslationRequest {
  file_id: string;
  source_lang: LanguageCode;
  target_lang: LanguageCode;
  llm_provider: LLMProvider;
  api_key?: string;
  api_base?: string;
  model?: string;
}

// 翻译进度
export interface TranslationProgress {
  task_id: string;
  status: TranslationStatus;
  progress: number;
  current_page: number;
  total_pages: number;
  message: string;
}

// 翻译结果
export interface TranslationResult {
  task_id: string;
  status: TranslationStatus;
  output_file_id?: string;
  download_url?: string;
  message: string;
}

// 错误响应
export interface ErrorResponse {
  error: string;
  detail?: string;
}

// 应用状态
export interface AppState {
  // 文件状态
  uploadedFile: FileUploadResponse | null;
  
  // 翻译配置
  sourceLang: LanguageCode;
  targetLang: LanguageCode;
  llmProvider: LLMProvider;
  apiKey: string;
  apiBase: string;
  model: string;
  
  // 翻译状态
  translationTask: TranslationProgress | null;
  translationResult: TranslationResult | null;
  
  // UI 状态
  isUploading: boolean;
  isTranslating: boolean;
  error: string | null;
}
