/**
 * API 服务
 */
import axios, { AxiosError } from 'axios';
import type {
  FileUploadResponse,
  TranslationRequest,
  TranslationProgress,
  TranslationResult,
  LanguageInfo,
  LLMProviderInfo,
  ErrorResponse,
} from '../types';

const api = axios.create({
  baseURL: '/api',
  timeout: 120000, // 2分钟超时
});

// 错误处理
const handleError = (error: AxiosError<ErrorResponse>): never => {
  if (error.response?.data?.detail) {
    throw new Error(error.response.data.detail);
  }
  if (error.response?.data?.error) {
    throw new Error(error.response.data.error);
  }
  throw new Error(error.message || '请求失败');
};

/**
 * 上传PDF文件
 */
export const uploadFile = async (file: File): Promise<FileUploadResponse> => {
  try {
    const formData = new FormData();
    formData.append('file', file);

    const response = await api.post<FileUploadResponse>('/files/upload', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });

    return response.data;
  } catch (error) {
    throw handleError(error as AxiosError<ErrorResponse>);
  }
};

/**
 * 开始翻译
 */
export const startTranslation = async (
  request: TranslationRequest
): Promise<TranslationProgress> => {
  try {
    const response = await api.post<TranslationProgress>('/translation/start', request);
    return response.data;
  } catch (error) {
    throw handleError(error as AxiosError<ErrorResponse>);
  }
};

/**
 * 获取翻译进度
 */
export const getTranslationProgress = async (
  taskId: string
): Promise<TranslationProgress> => {
  try {
    const response = await api.get<TranslationProgress>(`/translation/progress/${taskId}`);
    return response.data;
  } catch (error) {
    throw handleError(error as AxiosError<ErrorResponse>);
  }
};

/**
 * 获取翻译结果
 */
export const getTranslationResult = async (
  taskId: string
): Promise<TranslationResult> => {
  try {
    const response = await api.get<TranslationResult>(`/translation/result/${taskId}`);
    return response.data;
  } catch (error) {
    throw handleError(error as AxiosError<ErrorResponse>);
  }
};

/**
 * 下载翻译后的文件
 */
export const downloadFile = (fileId: string): void => {
  window.open(`/api/files/download/${fileId}`, '_blank');
};

/**
 * 删除文件
 */
export const deleteFile = async (fileId: string): Promise<void> => {
  try {
    await api.delete(`/files/${fileId}`);
  } catch (error) {
    throw handleError(error as AxiosError<ErrorResponse>);
  }
};

/**
 * 获取支持的语言列表
 */
export const getSupportedLanguages = async (): Promise<LanguageInfo[]> => {
  try {
    const response = await api.get<{ languages: LanguageInfo[] }>('/translation/languages');
    return response.data.languages;
  } catch (error) {
    throw handleError(error as AxiosError<ErrorResponse>);
  }
};

/**
 * 获取LLM提供者列表
 */
export const getLLMProviders = async (): Promise<LLMProviderInfo[]> => {
  try {
    const response = await api.get<{ providers: LLMProviderInfo[] }>('/config/llm-providers');
    return response.data.providers;
  } catch (error) {
    throw handleError(error as AxiosError<ErrorResponse>);
  }
};

/**
 * 健康检查
 */
export const healthCheck = async (): Promise<boolean> => {
  try {
    const response = await api.get('/health');
    return response.data.status === 'healthy';
  } catch {
    return false;
  }
};
