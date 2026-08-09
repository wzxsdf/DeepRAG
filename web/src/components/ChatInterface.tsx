// 聊天界面组件 - Zustand + SSE + Markdown

import { useState, useEffect, useRef, useCallback } from 'react';
import { useChatStore } from '@/store/chatStore';
import { Send, Loader2, AlertCircle, Copy, Check } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import rehypeHighlight from 'rehype-highlight';
import rehypeRaw from 'rehype-raw';

// 源码显示组件
interface SourceDisplayProps {
  sources: Array<{
    id: string;
    content: string;
    score?: number;
    metadata?: Record<string, any>;
  }>;
  onSourceClick?: (source: any) => void;
}

export function SourceDisplay({ sources, onSourceClick }: SourceDisplayProps) {
  if (!sources || sources.length === 0) return null;

  const [copiedId, setCopiedId] = useState<string | null>(null);

  const handleCopy = async (text: string, id: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(id);
      setTimeout(() => setCopiedId(null), 2000);
    } catch (error) {
      console.error('复制失败:', error);
    }
  };

  return (
    <div className="mt-4 p-4 bg-gray-50 rounded-lg border border-gray-200">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-semibold text-gray-700">📚 参考资料</h4>
        <span className="text-xs text-gray-500">{sources.length} 个来源</span>
      </div>

      <div className="space-y-2">
        {sources.map((source, index) => (
          <div
            key={source.id}
            className="p-3 bg-white rounded border border-gray-200 hover:border-blue-300 transition-colors cursor-pointer"
            onClick={() => onSourceClick?.(source)}
          >
            <div className="flex items-start justify-between">
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs font-mono text-gray-500">#{index + 1}</span>
                  {source.score && (
                    <span className="text-xs text-blue-600">
                      相关度: {(source.score * 100).toFixed(1)}%
                    </span>
                  )}
                </div>
                <p className="text-sm text-gray-700 line-clamp-2">
                  {source.content.length > 150
                    ? source.content.substring(0, 150) + '...'
                    : source.content}
                </p>
                {source.metadata && (
                  <div className="mt-2 flex gap-2">
                    {source.metadata.domain && (
                      <span className="text-xs bg-blue-100 text-blue-800 px-2 py-0.5 rounded">
                        {source.metadata.domain}
                      </span>
                    )}
                    {source.metadata.source && (
                      <span className="text-xs bg-green-100 text-green-800 px-2 py-0.5 rounded">
                        {source.metadata.source}
                      </span>
                    )}
                  </div>
                )}
              </div>
              <button
                onClick={() => handleCopy(source.content, source.id)}
                className="ml-2 p-1.5 hover:bg-gray-100 rounded transition-colors"
                title="复制内容"
              >
                {copiedId === source.id ? (
                  <Check className="w-4 h-4 text-green-600" />
                ) : (
                  <Copy className="w-4 h-4 text-gray-400" />
                )}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Markdown渲染器组件
interface MarkdownRendererProps {
  content: string;
  className?: string;
}

export function MarkdownRenderer({ content, className = '' }: MarkdownRendererProps) {
  return (
    <div className={`prose prose-sm max-w-none ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        rehypePlugins={[rehypeHighlight, rehypeRaw]}
        components={{
          code({ node, inline, className, children, ...props }: any) {
            const match = /language-(\w+)/.exec(className || '');
            const language = match ? match[1] : '';

            return !inline && match ? (
              <SyntaxHighlighter
                style={oneDark}
                language={language}
                PreTag="div"
                className="rounded-lg"
                {...props}
              >
                {String(children).replace(/\n$/, '')}
              </SyntaxHighlighter>
            ) : (
              <code className={className} {...props}>
                {children}
              </code>
            );
          },
          a({ href, children }) {
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-600 hover:text-blue-800 underline"
              >
                {children}
              </a>
            );
          }
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

// 主聊天界面组件
export function ChatInterface() {
  const {
    messages,
    currentInput,
    isLoading,
    isStreaming,
    error,
    currentSessionId,
    sessions,
    setCurrentInput,
    addMessage,
    updateMessage,
    clearMessages,
    setLoading,
    startStreaming,
    createSession,
    loadSessions
  } = useChatStore();

  const [streamingText, setStreamingText] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // 加载会话列表
  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  // 自动滚动到底部
  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, streamingText, scrollToBottom]);

  // 处理发送消息
  const handleSendMessage = async () => {
    if (!currentInput.trim() || isLoading || isStreaming) return;

    const question = currentInput.trim();
    const sessionId = currentSessionId || createSession();

    // 清空输入
    setCurrentInput('');

    try {
      // 启动SSE流式接收
      const streamGenerator = startStreaming(question, sessionId);

      for await (const token of streamGenerator) {
        setStreamingText((prev) => prev + token);
      }
    } catch (error) {
      console.error('发送消息失败:', error);
    } finally {
      setStreamingText('');
    }
  };

  // 处理键盘事件
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  // 创建新对话
  const handleNewChat = () => {
    clearMessages();
    createSession();
  };

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* 顶部导航栏 */}
      <div className="flex items-center justify-between px-6 py-4 bg-white border-b border-gray-200">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold text-gray-800">DeepRAG 智能助手</h1>
          {error && (
            <div className="flex items-center gap-2 text-red-600 text-sm">
              <AlertCircle className="w-4 h-4" />
              <span>{error}</span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleNewChat}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
          >
            新对话
          </button>
        </div>
      </div>

      {/* 主聊天区域 */}
      <div className="flex-1 overflow-hidden">
        <div className="h-full max-w-4xl mx-auto flex flex-col">
          {/* 消息列表 */}
          <div className="flex-1 overflow-y-auto p-6 space-y-6">
            {messages.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-center">
                <div className="text-6xl mb-4">🤖</div>
                <h2 className="text-xl font-semibold text-gray-700 mb-2">
                  欢迎使用 DeepRAG 智能助手
                </h2>
                <p className="text-gray-500 mb-6">
                  我是您的AI助手，可以帮您检索知识库、解答问题、提供分析
                </p>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 max-w-2xl">
                  <div className="p-4 bg-white rounded-lg border border-gray-200 hover:border-blue-300 transition-colors cursor-pointer"
                       onClick={() => setCurrentInput('DeepRAG的检索层用了什么技术？')}>
                    <div className="text-sm font-medium text-gray-800">技术介绍</div>
                    <div className="text-xs text-gray-500">了解DeepRAG技术栈</div>
                  </div>
                  <div className="p-4 bg-white rounded-lg border border-gray-200 hover:border-blue-300 transition-colors cursor-pointer"
                       onClick={() => setCurrentInput('如何使用知识库功能？')}>
                    <div className="text-sm font-medium text-gray-800">使用指南</div>
                    <div className="text-xs text-gray-500">学习知识库操作</div>
                  </div>
                  <div className="p-4 bg-white rounded-lg border border-gray-200 hover:border-blue-300 transition-colors cursor-pointer"
                       onClick={() => setCurrentInput('系统支持哪些功能？')}>
                    <div className="text-sm font-medium text-gray-800">功能概览</div>
                    <div className="text-xs text-gray-500">探索系统功能</div>
                  </div>
                </div>
              </div>
            ) : (
              messages.map((message) => (
                <div
                  key={message.id}
                  className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  <div
                    className={`max-w-3xl rounded-2xl px-6 py-4 ${
                      message.role === 'user'
                        ? 'bg-blue-600 text-white'
                        : 'bg-white border border-gray-200 shadow-sm'
                    }`}
                  >
                    {message.role === 'assistant' ? (
                      <>
                        <MarkdownRenderer
                          content={message.content + (message.isStreaming ? '▌' : '')}
                          className="text-gray-800"
                        />
                        {message.error && (
                          <div className="mt-2 text-sm text-red-600 bg-red-50 px-3 py-2 rounded">
                            错误: {message.error}
                          </div>
                        )}
                        {message.sources && message.sources.length > 0 && (
                          <SourceDisplay
                            sources={message.sources}
                          />
                        )}
                      </>
                    ) : (
                      <div className="text-white whitespace-pre-wrap">{message.content}</div>
                    )}
                  </div>
                </div>
              ))
            )}

            {/* 流式输出的临时文本 */}
            {streamingText && (
              <div className="flex justify-start">
                <div className="max-w-3xl rounded-2xl px-6 py-4 bg-white border border-gray-200 shadow-sm">
                  <MarkdownRenderer
                    content={streamingText + '▌'}
                    className="text-gray-800"
                  />
                </div>
              </div>
            )}

            {/* 加载指示器 */}
            {isLoading && !isStreaming && (
              <div className="flex justify-start">
                <div className="px-6 py-4 bg-white rounded-2xl border border-gray-200">
                  <div className="flex items-center gap-2 text-gray-500">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span className="text-sm">思考中...</span>
                  </div>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* 输入区域 */}
          <div className="border-t border-gray-200 bg-white p-6">
            <div className="flex items-end gap-3">
              <textarea
                ref={inputRef}
                value={currentInput}
                onChange={(e) => setCurrentInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="输入您的问题... (Enter发送，Shift+Enter换行)"
                className="flex-1 min-h-[60px] max-h-[200px] p-4 border border-gray-300 rounded-lg resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                disabled={isLoading || isStreaming}
                rows={1}
              />
              <button
                onClick={handleSendMessage}
                disabled={!currentInput.trim() || isLoading || isStreaming}
                className="px-6 py-4 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
              >
                {isLoading || isStreaming ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : (
                  <>
                    <span>发送</span>
                    <Send className="w-5 h-5" />
                  </>
                )}
              </button>
            </div>

            {/* 提示文本 */}
            <div className="mt-2 text-xs text-gray-500">
              <p>💡 提示：按 Enter 发送，Shift+Enter 换行 • 支持Markdown格式 • 引用显示来源</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ChatInterface;
