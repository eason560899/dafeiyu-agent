export type Json = Record<string, unknown>
export type Provider = { id?: string; name: string; model: string; endpoint?: string; status?: string; available?: boolean }
export type Candidate = { id: string; conversation_id?: string; content?: string; title?: string; summary?: string; provider?: string; model?: string; score?: number | null; selected?: boolean; status?: string; created_at?: string }
export type Action = { id: string; conversation_id?: string; type?: string; verb?: string; target?: string; payload?: Json; status?: string; result?: string; created_at?: string }
export type Connection = { id: string; name: string; kind: string; config?: Json; status?: string; description?: string }
export type Health = { status?: string; service?: string; mock_mode?: boolean; uptime?: string; latency?: string; queue?: number; updatedAt?: string }
export type Guardrails = { autonomous_mode?: boolean; enabled?: boolean; require_confirmation?: boolean; paused?: boolean; circuit_breakers?: string[]; global_stop?: boolean; paused_platforms?: string[]; [key: string]: unknown }
export type TaskConfig = { enabled?: boolean; schedule?: string; sources?: string[]; prompt?: string; [key: string]: unknown }

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')
const TOKEN_KEY = 'dafeiyu_device_token'

export function getDeviceToken(): string { return window.localStorage.getItem(TOKEN_KEY) || '' }
export function setDeviceToken(token: string): void { if (token.trim()) window.localStorage.setItem(TOKEN_KEY, token.trim()) }
export function clearDeviceToken(): void { window.localStorage.removeItem(TOKEN_KEY) }

export async function request<T>(path: string, options: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.json !== undefined) headers.set('Content-Type', 'application/json')
  const token = getDeviceToken()
  if (token) headers.set('X-Device-Token', token)
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    body: options.json === undefined ? options.body : JSON.stringify(options.json),
  })
  const text = await response.text()
  let body: unknown = null
  try { body = text ? JSON.parse(text) : null } catch { body = text }
  if (!response.ok) {
    const detail = typeof body === 'object' && body && 'detail' in body ? String((body as Json).detail) : `请求失败（${response.status}）`
    throw new Error(detail)
  }
  return body as T
}

async function requestAny<T>(paths: string[], options: RequestInit & { json?: unknown } = {}): Promise<T> {
  let last: unknown = new Error('请求失败')
  for (const path of paths) {
    try { return await request<T>(path, options) } catch (error) { last = error }
  }
  throw last
}

export async function streamChat(payload: { message: string; conversation_id?: string; context?: Json }, onMessage: (message: string, data: Json) => void, onEvent?: (data: Json) => void, onDelta?: (delta: string) => void): Promise<string | undefined> {
  const headers = new Headers({ 'Content-Type': 'application/json', Accept: 'application/x-ndjson' })
  const token = getDeviceToken(); if (token) headers.set('X-Device-Token', token)
  const response = await fetch(`${API_BASE}/chat/stream`, { method: 'POST', headers, body: JSON.stringify(payload) })
  if (!response.ok || !response.body) throw new Error(`流式对话失败（${response.status}）`)
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; let conversationId: string | undefined
  while (true) {
    const { done, value } = await reader.read(); if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n'); buffer = lines.pop() || ''
    for (const line of lines) {
      if (!line.trim()) continue
      try {
        const item = JSON.parse(line) as { type?: string; data?: Json }
        if (item.type === 'delta') onDelta?.(String(item.data || ''))
        else if (item.type === 'message' && item.data) { conversationId = String(item.data.conversation_id || '') || conversationId; onMessage(String(item.data.message || ''), item.data) }
        else if (item.type === 'event' && item.data) onEvent?.(item.data)
        else if (item.type === 'error') throw new Error(String(item.data?.message || '流式对话失败'))
      } catch (error) { if (error instanceof Error && error.message !== 'Unexpected end of JSON input') throw error }
    }
  }
  return conversationId
}

export const api = {
  health: () => request<Health>('/health'),
  providers: () => request<Provider[]>('/providers'),
  saveProvider: (payload: { name: string; endpoint: string; model: string; key?: string }) => request<Provider>('/providers', { method: 'POST', json: payload }),
  candidates: () => request<Candidate[]>('/candidates'),
  saveTaskConfig: (payload: TaskConfig) => request<TaskConfig>('/tasks/config', { method: 'PUT', json: payload }),
  approveCandidate: (id: string) => request<Candidate>(`/candidates/${encodeURIComponent(id)}/approve`, { method: 'POST' }),
  actions: () => request<Action[]>('/actions'),
  createAction: (payload: Json) => request<Action>('/actions', { method: 'POST', json: payload }),
  approveAction: (id: string) => request<Action>(`/actions/${encodeURIComponent(id)}/approve`, { method: 'POST' }),
  rejectAction: (id: string) => request<Action>(`/actions/${encodeURIComponent(id)}/reject`, { method: 'POST' }),
  executeAction: (id: string) => request<Action>(`/actions/${encodeURIComponent(id)}/execute`, { method: 'POST' }),
  connections: () => request<Connection[]>('/connections'),
  createConnection: (payload: Json) => request<Connection>('/connections', { method: 'POST', json: payload }),
  updateConnection: (id: string, payload: Json) => request<Connection>(`/connections/${encodeURIComponent(id)}`, { method: 'PUT', json: payload }),
  connectionStatus: (id: string) => request<Json>(`/connections/${encodeURIComponent(id)}/status`),
  connect: (id: string) => request<Connection>(`/connections/${encodeURIComponent(id)}/connect`, { method: 'POST' }),
  guardrails: () => request<Guardrails>('/automation/guardrails'),
  stop: () => request<Guardrails>('/automation/stop', { method: 'POST' }),
  resume: () => request<Guardrails>('/automation/resume', { method: 'POST' }),
  pausePlatform: (id: string) => request<Connection>(`/automation/platforms/${encodeURIComponent(id)}/pause`, { method: 'POST' }),
  pairDevice: (payload: Json) => requestAny<{ token?: string; device_token?: string; device?: Json }>(['/auth/device/pair', '/device/pair', '/auth/pair'], { method: 'POST', json: payload }),
  deviceStatus: () => requestAny<Json>(['/auth/device', '/device'], {}),
}

export function candidateView(item: Candidate): Candidate & { title: string; summary: string; scoreLabel: string; state: string } {
  const content = item.content || item.summary || ''
  const title = item.title || content.split(/[。.!！？\n]/)[0] || '未命名候选'
  const score = typeof item.score === 'number' ? `${Math.round(item.score <= 1 ? item.score * 100 : item.score)}%` : '—'
  const state = item.status || (item.selected === true ? '已批准' : '待审核')
  return { ...item, title, summary: item.summary || content, scoreLabel: score, state }
}

export function actionView(item: Action): Action & { verb: string; targetLabel: string; resultLabel: string } {
  const result = item.result || ({ approved: '已批准', rejected: '已拒绝', executed: '已执行', completed: '已完成', pending: '等待中', failed: '失败' } as Record<string, string>)[item.status || ''] || item.status || '未知状态'
  return { ...item, verb: item.verb || item.type || '动作', targetLabel: item.target || String(item.payload?.target || item.payload?.candidate_id || '未指明目标'), resultLabel: result }
}
