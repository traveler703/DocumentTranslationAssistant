/**
 * 翻译配置组件
 */
import { useState, useEffect } from 'react';
import { Settings, Key, Server, Cpu } from 'lucide-react';
import type { LanguageCode, LLMProvider, LanguageInfo, LLMProviderInfo } from '../types';
import { getSupportedLanguages, getLLMProviders } from '../services/api';

interface TranslationConfigProps {
  sourceLang: LanguageCode;
  targetLang: LanguageCode;
  llmProvider: LLMProvider;
  apiKey: string;
  apiBase: string;
  model: string;
  onSourceLangChange: (lang: LanguageCode) => void;
  onTargetLangChange: (lang: LanguageCode) => void;
  onLLMProviderChange: (provider: LLMProvider) => void;
  onApiKeyChange: (key: string) => void;
  onApiBaseChange: (base: string) => void;
  onModelChange: (model: string) => void;
  disabled?: boolean;
}

export function TranslationConfig({
  sourceLang,
  targetLang,
  llmProvider,
  apiKey,
  apiBase,
  model,
  onSourceLangChange,
  onTargetLangChange,
  onLLMProviderChange,
  onApiKeyChange,
  onApiBaseChange,
  onModelChange,
  disabled = false,
}: TranslationConfigProps) {
  const [languages, setLanguages] = useState<LanguageInfo[]>([]);
  const [providers, setProviders] = useState<LLMProviderInfo[]>([]);
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(() => {
    // 加载配置
    Promise.all([getSupportedLanguages(), getLLMProviders()])
      .then(([langs, provs]) => {
        setLanguages(langs);
        setProviders(provs);
      })
      .catch(console.error);
  }, []);

  const currentProvider = providers.find((p) => p.id === llmProvider);

  return (
    <div className="card">
      <div className="flex items-center space-x-2 mb-6">
        <Settings className="w-5 h-5 text-primary-600" />
        <h2 className="text-lg font-semibold text-gray-900">翻译设置</h2>
      </div>

      <div className="space-y-6">
        {/* 语言选择 */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label">源语言</label>
            <select
              className="select"
              value={sourceLang}
              onChange={(e) => onSourceLangChange(e.target.value as LanguageCode)}
              disabled={disabled}
            >
              {languages.map((lang) => (
                <option key={lang.code} value={lang.code}>
                  {lang.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">目标语言</label>
            <select
              className="select"
              value={targetLang}
              onChange={(e) => onTargetLangChange(e.target.value as LanguageCode)}
              disabled={disabled}
            >
              {languages.map((lang) => (
                <option key={lang.code} value={lang.code}>
                  {lang.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* LLM 提供者 */}
        <div>
          <label className="label flex items-center space-x-1">
            <Cpu className="w-4 h-4" />
            <span>翻译引擎</span>
          </label>
          <div className="grid grid-cols-3 gap-3">
            {providers.map((provider) => (
              <button
                key={provider.id}
                onClick={() => onLLMProviderChange(provider.id)}
                disabled={disabled}
                className={`p-3 rounded-lg border-2 transition-all text-left ${
                  llmProvider === provider.id
                    ? 'border-primary-500 bg-primary-50'
                    : 'border-gray-200 hover:border-gray-300'
                } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
              >
                <div className="font-medium text-sm text-gray-900">{provider.name}</div>
                <div className="text-xs text-gray-500 mt-1">{provider.description}</div>
              </button>
            ))}
          </div>
        </div>

        {/* API Key (仅 OpenAI) */}
        {currentProvider?.requires_api_key && (
          <div>
            <label className="label flex items-center space-x-1">
              <Key className="w-4 h-4" />
              <span>API Key</span>
            </label>
            <input
              type="password"
              className="input"
              placeholder="输入你的 API Key"
              value={apiKey}
              onChange={(e) => onApiKeyChange(e.target.value)}
              disabled={disabled}
            />
            <p className="text-xs text-gray-500 mt-1">
              你的 API Key 仅用于本次翻译，不会被存储
            </p>
          </div>
        )}

        {/* 高级设置 */}
        <div>
          <button
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="text-sm text-primary-600 hover:text-primary-700 font-medium"
          >
            {showAdvanced ? '收起高级设置' : '展开高级设置'}
          </button>

          {showAdvanced && (
            <div className="mt-4 space-y-4 p-4 bg-gray-50 rounded-lg">
              <div>
                <label className="label flex items-center space-x-1">
                  <Server className="w-4 h-4" />
                  <span>API Base URL（可选）</span>
                </label>
                <input
                  type="text"
                  className="input"
                  placeholder="https://api.openai.com/v1"
                  value={apiBase}
                  onChange={(e) => onApiBaseChange(e.target.value)}
                  disabled={disabled}
                />
                <p className="text-xs text-gray-500 mt-1">
                  自定义 API 端点，用于兼容其他服务
                </p>
              </div>
              <div>
                <label className="label">模型名称（可选）</label>
                <input
                  type="text"
                  className="input"
                  placeholder="gpt-4"
                  value={model}
                  onChange={(e) => onModelChange(e.target.value)}
                  disabled={disabled}
                />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
