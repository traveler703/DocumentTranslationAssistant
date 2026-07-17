/**
 * 文档翻译助手 - 主应用
 */
import { useState, useCallback } from 'react';
import { FileText, Languages, Zap } from 'lucide-react';
import {
  FileUpload,
  TranslationConfig,
  TranslationProgressPanel,
  TranslationResultPanel,
} from './components';
import { uploadFile, startTranslation } from './services/api';
import type {
  LanguageCode,
  LLMProvider,
  FileUploadResponse,
  TranslationProgress,
  TranslationResult,
} from './types';

type AppStep = 'upload' | 'config' | 'translating' | 'complete';

function App() {
  // 当前步骤
  const [step, setStep] = useState<AppStep>('upload');

  // 文件状态
  const [uploadedFile, setUploadedFile] = useState<FileUploadResponse | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // 翻译配置
  const [sourceLang, setSourceLang] = useState<LanguageCode>('en');
  const [targetLang, setTargetLang] = useState<LanguageCode>('zh-CN');
  const [llmProvider, setLLMProvider] = useState<LLMProvider>('openai');
  const [apiKey, setApiKey] = useState('');
  const [apiBase, setApiBase] = useState('');
  const [model, setModel] = useState('');

  // 翻译状态
  const [translationTask, setTranslationTask] = useState<TranslationProgress | null>(null);
  const [translationResult, setTranslationResult] = useState<TranslationResult | null>(null);
  const [translationError, setTranslationError] = useState<string | null>(null);

  // 处理文件上传
  const handleUpload = useCallback(async (file: File) => {
    setIsUploading(true);
    setUploadError(null);

    try {
      const result = await uploadFile(file);
      setUploadedFile(result);
      setStep('config');
    } catch (error) {
      setUploadError((error as Error).message);
    } finally {
      setIsUploading(false);
    }
  }, []);

  // 清除上传的文件
  const handleClearFile = useCallback(() => {
    setUploadedFile(null);
    setStep('upload');
    setTranslationTask(null);
    setTranslationResult(null);
    setTranslationError(null);
  }, []);

  // 开始翻译
  const handleStartTranslation = useCallback(async () => {
    if (!uploadedFile) return;

    setTranslationError(null);

    try {
      const task = await startTranslation({
        file_id: uploadedFile.file_id,
        source_lang: sourceLang,
        target_lang: targetLang,
        llm_provider: llmProvider,
        api_key: apiKey || undefined,
        api_base: apiBase || undefined,
        model: model || undefined,
      });

      setTranslationTask(task);
      setStep('translating');
    } catch (error) {
      setTranslationError((error as Error).message);
    }
  }, [uploadedFile, sourceLang, targetLang, llmProvider, apiKey, apiBase, model]);

  // 翻译完成
  const handleTranslationComplete = useCallback((result: TranslationResult) => {
    setTranslationResult(result);
    setStep('complete');
  }, []);

  // 翻译错误
  const handleTranslationError = useCallback((error: string) => {
    setTranslationError(error);
  }, []);

  // 重新开始
  const handleNewTranslation = useCallback(() => {
    setUploadedFile(null);
    setTranslationTask(null);
    setTranslationResult(null);
    setTranslationError(null);
    setStep('upload');
  }, []);

  // 检查是否可以开始翻译
  const canStartTranslation =
    uploadedFile &&
    (llmProvider !== 'openai' || apiKey.trim() !== '');

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50">
      {/* 头部 */}
      <header className="bg-white border-b border-gray-200">
        <div className="max-w-4xl mx-auto px-4 py-4">
          <div className="flex items-center space-x-3">
            <div className="w-10 h-10 bg-primary-600 rounded-lg flex items-center justify-center">
              <FileText className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-gray-900">文档翻译助手</h1>
              <p className="text-sm text-gray-500">智能PDF翻译，保留原文排版</p>
            </div>
          </div>
        </div>
      </header>

      {/* 主内容 */}
      <main className="max-w-4xl mx-auto px-4 py-8">
        {/* 步骤指示器 */}
        <div className="flex items-center justify-center mb-8">
          <div className="flex items-center space-x-4">
            <StepIndicator
              number={1}
              label="上传文档"
              active={step === 'upload'}
              completed={step !== 'upload'}
            />
            <div className="w-12 h-0.5 bg-gray-200" />
            <StepIndicator
              number={2}
              label="配置翻译"
              active={step === 'config'}
              completed={step === 'translating' || step === 'complete'}
            />
            <div className="w-12 h-0.5 bg-gray-200" />
            <StepIndicator
              number={3}
              label="获取结果"
              active={step === 'translating' || step === 'complete'}
              completed={step === 'complete'}
            />
          </div>
        </div>

        {/* 内容区域 */}
        <div className="space-y-6">
          {/* 文件上传（始终显示） */}
          {(step === 'upload' || step === 'config') && (
            <FileUpload
              onUpload={handleUpload}
              uploadedFile={uploadedFile}
              onClear={handleClearFile}
              isUploading={isUploading}
              error={uploadError}
            />
          )}

          {/* 翻译配置 */}
          {step === 'config' && (
            <>
              <TranslationConfig
                sourceLang={sourceLang}
                targetLang={targetLang}
                llmProvider={llmProvider}
                apiKey={apiKey}
                apiBase={apiBase}
                model={model}
                onSourceLangChange={setSourceLang}
                onTargetLangChange={setTargetLang}
                onLLMProviderChange={setLLMProvider}
                onApiKeyChange={setApiKey}
                onApiBaseChange={setApiBase}
                onModelChange={setModel}
              />

              {/* 错误提示 */}
              {translationError && (
                <div className="p-4 bg-red-50 border border-red-200 rounded-lg">
                  <p className="text-sm text-red-600">{translationError}</p>
                </div>
              )}

              {/* 开始翻译按钮 */}
              <button
                onClick={handleStartTranslation}
                disabled={!canStartTranslation}
                className="btn btn-primary w-full py-3 text-lg flex items-center justify-center space-x-2"
              >
                <Zap className="w-5 h-5" />
                <span>开始翻译</span>
              </button>
            </>
          )}

          {/* 翻译进度 */}
          {step === 'translating' && translationTask && (
            <TranslationProgressPanel
              taskId={translationTask.task_id}
              onComplete={handleTranslationComplete}
              onError={handleTranslationError}
            />
          )}

          {/* 翻译结果 */}
          {step === 'complete' && translationResult && (
            <TranslationResultPanel
              result={translationResult}
              onNewTranslation={handleNewTranslation}
            />
          )}
        </div>

        {/* 功能说明 */}
        {step === 'upload' && (
          <div className="mt-12 grid grid-cols-3 gap-6">
            <FeatureCard
              icon={<FileText className="w-6 h-6" />}
              title="智能布局识别"
              description="自动识别分栏、段落分页，准确提取文本内容"
            />
            <FeatureCard
              icon={<Languages className="w-6 h-6" />}
              title="7种语言互译"
              description="支持中、英、法、德、西、日等主流语言"
            />
            <FeatureCard
              icon={<Zap className="w-6 h-6" />}
              title="保留原始排版"
              description="翻译后的PDF保持原文档的格式和布局"
            />
          </div>
        )}
      </main>

      {/* 页脚 */}
      <footer className="mt-auto py-6 text-center text-sm text-gray-500">
        <p>Document Translation Assistant v1.0.0</p>
      </footer>
    </div>
  );
}

// 步骤指示器组件
function StepIndicator({
  number,
  label,
  active,
  completed,
}: {
  number: number;
  label: string;
  active: boolean;
  completed: boolean;
}) {
  return (
    <div className="flex flex-col items-center">
      <div
        className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium transition-all ${
          completed
            ? 'bg-primary-600 text-white'
            : active
            ? 'bg-primary-100 text-primary-600 ring-2 ring-primary-600'
            : 'bg-gray-100 text-gray-400'
        }`}
      >
        {completed ? '✓' : number}
      </div>
      <span
        className={`mt-2 text-xs font-medium ${
          active || completed ? 'text-gray-900' : 'text-gray-400'
        }`}
      >
        {label}
      </span>
    </div>
  );
}

// 功能卡片组件
function FeatureCard({
  icon,
  title,
  description,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div className="text-center p-6">
      <div className="inline-flex items-center justify-center w-12 h-12 bg-primary-100 text-primary-600 rounded-lg mb-4">
        {icon}
      </div>
      <h3 className="font-semibold text-gray-900 mb-2">{title}</h3>
      <p className="text-sm text-gray-500">{description}</p>
    </div>
  );
}

export default App;
