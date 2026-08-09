// 聊天状态管理 - Zustand store

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

// 消息类型
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  sources?: SourceInfo[];
  isStreaming?: boolean;
  error?: string;
}

// 源码信息
export interface SourceInfo {
  id: string;
  content: string;
  score?: number;
  metadata?: Record<string, any>;
}

// 会话信息
export interface ConversationSession {
  id: string;
  title: string;
  createdAt: Date;
  lastActivity: Date;
  messageCount: number;
}

// 聊天状态接口
interface ChatState {
  // 当前消息
  messages: ChatMessage[];

  // 当前输入
  currentInput: string;

  // 会话管理
  currentSessionId: string | null;
  sessions: ConversationSession[];

  // UI状态
  isLoading: boolean;
  isStreaming: boolean;
  error: string | null;

  // 设置
  maxHistory: number;

  // Actions
  setCurrentInput: (input: string) => void;
  addMessage: (message: Omit<ChatMessage, 'id'>) => string;
  updateMessage: (id: string, updates: Partial<ChatMessage>) => void;
  removeMessage: (id: string) => void;
  clearMessages: () => void;

  setLoading: (loading: boolean) => void;
  setStreaming: (streaming: boolean) => void;
  setError: (error: string | null) => void;

  setCurrentSession: (sessionId: string | null) => void;
  createSession: () => string;
  loadSessions: () => Promise<void>;

  // SSE相关
  startStreaming: (question: string, sessionId: string) => AsyncGenerator<string, void, void>;

  // 重置
  reset: () => void;
}

// 创建聊天store
export const useChatStore = create<ChatState>()(
  devtools(
    (set, get) => ({
      // 初始状态
      messages: [],
      currentInput: '',
      currentSessionId: null,
      sessions: [],
      isLoading: false,
      isStreaming: false,
      error: null,
      maxHistory: 50,

      // 设置当前输入
      setCurrentInput: (input: string) => {
        set({ currentInput: input });
      },

      // 添加消息
      addMessage: (message) => {
        const id = `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
        const newMessage: ChatMessage = {
          id,
          timestamp: new Date(),
          isStreaming: message.role === 'assistant',
          ...message
        };

        set((state) => ({
          messages: [...state.messages, newMessage]
        }));

        return id;
      },

      // 更新消息
      updateMessage: (id, updates) => {
        set((state) => ({
          messages: state.messages.map(msg =>
            msg.id === id ? { ...msg, ...updates } : msg
          )
        }));
      },

      // 删除消息
      removeMessage: (id) => {
        set((state) => ({
          messages: state.messages.filter(msg => msg.id !== id)
        }));
      },

      // 清空消息
      clearMessages: () => {
        set({ messages: [] });
      },

      // 设置加载状态
      setLoading: (loading) => {
        set({ isLoading: loading });
      },

      // 设置流式状态
      setStreaming: (streaming) => {
        set({ isStreaming: streaming });
      },

      // 设置错误
      setError: (error) => {
        set({ error });
      },

      // 设置当前会话
      setCurrentSession: (sessionId) => {
        set({ currentSessionId: sessionId });
      },

      // 创建新会话
      createSession: () => {
        const sessionId = `session-${Date.now()}`;
        const newSession: ConversationSession = {
          id: sessionId,
          title: '新对话',
          createdAt: new Date(),
          lastActivity: new Date(),
          messageCount: 0
        };

        set((state) => ({
          sessions: [newSession, ...state.sessions],
          currentSessionId: sessionId,
          messages: []
        }));

        return sessionId;
      },

      // 加载会话列表
      loadSessions: async () => {
        try {
          const response = await fetch('/api/v1/sessions', {
            method: 'GET',
            headers: {
              'Content-Type': 'application/json',
            }
          });

          if (response.ok) {
            const data = await response.json();
            set({ sessions: data.sessions || [] });
          }
        } catch (error) {
          console.error('加载会话列表失败:', error);
        }
      },

      // SSE流式请求
      startStreaming: async function* (question: string, sessionId: string) {
        const store = get();
        store.setLoading(true);
        store.setStreaming(true);
        store.setError(null);

        // 添加用户消息
        const userMessageId = store.addMessage({
          role: 'user',
          content: question
        });

        // 准备助手消息
        const assistantMessageId = store.addMessage({
          role: 'assistant',
          content: '',
          sources: []
        });

        try {
          const response = await fetch('/api/v1/query/stream', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              question,
              conversation_id: sessionId,
              stream: true
            })
          });

          if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
          }

          const reader = response.body?.getReader();
          if (!reader) {
            throw new Error('Response body is null');
          }

          const decoder = new TextDecoder();
          let buffer = '';

          while (true) {
            const { done, value } = await reader.read();

            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
              if (line.startsWith('data: ')) {
                try {
                  const data = JSON.parse(line.slice(6));

                  switch (data.type) {
                    case 'token':
                      // Token级别更新
                      store.updateMessage(assistantMessageId, {
                        content: (store.messages.find(m => m.id === assistantMessageId)?.content || '') + data.content
                      });
                      yield data.content;
                      break;

                    case 'source':
                      // 源码信息
                      store.updateMessage(assistantMessageId, {
                        sources: [...(store.messages.find(m => m.id === assistantMessageId)?.sources || []), data.source]
                      });
                      break;

                    case 'thought':
                      // 思考过程（可选显示）
                      console.log('思考过程:', data.content);
                      break;

                    case 'complete':
                      // 完成标志
                      store.updateMessage(assistantMessageId, {
                        isStreaming: false
                      });
                      break;

                    case 'error':
                      // 错误信息
                      store.updateMessage(assistantMessageId, {
                        error: data.content,
                        isStreaming: false
                      });
                      store.setError(data.content);
                      break;

                    default:
                      console.log('未知消息类型:', data);
                  }
                } catch (parseError) {
                  console.error('解析SSE数据失败:', parseError);
                }
              }
            }
          }
        } catch (error) {
          const errorMessage = error instanceof Error ? error.message : '网络请求失败';
          store.setError(errorMessage);
          store.updateMessage(assistantMessageId, {
            error: errorMessage,
            isStreaming: false
          });
        } finally {
          store.setLoading(false);
          store.setStreaming(false);
        }
      },

      // 重置状态
      reset: () => {
        set({
          messages: [],
          currentInput: '',
          isLoading: false,
          isStreaming: false,
          error: null
        });
      }
    }),
    {
      name: 'ChatStore'
    }
  )
);