/**
 * 翻译进度组件
 */
import { useEffect, useState } from 'react';
import { Loader2, CheckCircle, XCircle, Download, RefreshCw } from 'lucide-react';
import type { TranslationProgress, TranslationResult, TranslationStatus } from '../types';
import { getTranslationProgress, getTranslationResult, downloadFile } from '../services/api';

interface TranslationProgressProps {
  taskId: string;
  onComplete: (result: TranslationResult) => void;
  onError: (error: string) => void;
}

export function TranslationProgressPanel({
  taskId,
  onComplete,
  onError,
}: TranslationProgressProps) {
  const [progress, setProgress] = useState<TranslationProgress | null>(null);
  const [result, setResult] = useState<TranslationResult | null>(null);

  useEffect(() => {
    let intervalId: number;

    const pollProgress = async () => {
      try {
        const progressData = await getTranslationProgress(taskId);
        setProgress(progressData);

        if (progressData.status === 'completed') {
          const resultData = await getTranslationResult(taskId);
          setResult(resultData);
          onComplete(resultData);
          clearInterval(intervalId);
        } else if (progressData.status === 'failed') {
          onError(progressData.message || '翻译失败');
          clearInterval(intervalId);
        }
      } catch (error) {
        onError((error as Error).message);
        clearInterval(intervalId);
      }
    };

    // 立即执行一次
    pollProgress();

    // 每2秒轮询一次
    intervalId = window.setInterval(pollProgress, 2000);

    return () => {
      clearInterval(intervalId);
    };
  }, [taskId, onComplete, onError]);

  const getStatusIcon = (status: TranslationStatus) => {
    switch (status) {
      case 'pending':
      case 'processing':
        return <Loader2 className="w-6 h-6 text-primary-500 animate-spin" />;
      case 'completed':
        return <CheckCircle className="w-6 h-6 text-green-500" />;
      case 'failed':
        return <XCircle className="w-6 h-6 text-red-500" />;
    }
  };

  const getStatusBadge = (status: TranslationStatus) => {
    const badges: Record<TranslationStatus, { class: string; text: string }> = {
      pending: { class: 'badge-pending', text: '等待中' },
      processing: { class: 'badge-processing', text: '翻译中' },
      completed: { class: 'badge-completed', text: '已完成' },
      failed: { class: 'badge-failed', text: '失败' },
    };
    return badges[status];
  };

  if (!progress) {
    return (
      <div className="card">
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-8 h-8 text-primary-500 animate-spin" />
        </div>
      </div>
    );
  }

  const badge = getStatusBadge(progress.status);

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center space-x-3">
          {getStatusIcon(progress.status)}
          <div>
            <h3 className="font-semibold text-gray-900">翻译进度</h3>
            <span className={`badge ${badge.class}`}>{badge.text}</span>
          </div>
        </div>
        {progress.total_pages > 0 && (
          <span className="text-sm text-gray-500">
            {progress.current_page} / {progress.total_pages} 页
          </span>
        )}
      </div>

      {/* 进度条 */}
      <div className="mb-4">
        <div className="progress-bar">
          <div
            className="progress-bar-fill"
            style={{ width: `${progress.progress}%` }}
          />
        </div>
        <div className="flex justify-between mt-1">
          <span className="text-sm text-gray-500">{progress.message}</span>
          <span className="text-sm font-medium text-gray-700">
            {progress.progress.toFixed(0)}%
          </span>
        </div>
      </div>

      {/* 完成后显示下载按钮 */}
      {progress.status === 'completed' && result?.output_file_id && (
        <div className="pt-4 border-t border-gray-100">
          <button
            onClick={() => downloadFile(result.output_file_id!)}
            className="btn btn-primary w-full flex items-center justify-center space-x-2"
          >
            <Download className="w-5 h-5" />
            <span>下载翻译结果</span>
          </button>
        </div>
      )}

      {/* 失败后显示错误信息 */}
      {progress.status === 'failed' && (
        <div className="pt-4 border-t border-gray-100">
          <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
            <p className="text-sm text-red-600">{progress.message}</p>
          </div>
        </div>
      )}
    </div>
  );
}

interface TranslationResultPanelProps {
  result: TranslationResult;
  onNewTranslation: () => void;
}

export function TranslationResultPanel({
  result,
  onNewTranslation,
}: TranslationResultPanelProps) {
  return (
    <div className="card">
      <div className="flex items-center space-x-3 mb-6">
        <CheckCircle className="w-8 h-8 text-green-500" />
        <div>
          <h3 className="text-lg font-semibold text-gray-900">翻译完成</h3>
          <p className="text-sm text-gray-500">你的文档已成功翻译</p>
        </div>
      </div>

      <div className="space-y-3">
        {result.output_file_id && (
          <button
            onClick={() => downloadFile(result.output_file_id!)}
            className="btn btn-primary w-full flex items-center justify-center space-x-2"
          >
            <Download className="w-5 h-5" />
            <span>下载翻译结果</span>
          </button>
        )}

        <button
          onClick={onNewTranslation}
          className="btn btn-secondary w-full flex items-center justify-center space-x-2"
        >
          <RefreshCw className="w-5 h-5" />
          <span>翻译新文档</span>
        </button>
      </div>
    </div>
  );
}
