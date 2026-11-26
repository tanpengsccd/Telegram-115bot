export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

let unauthorizedHandler: (() => void) | null = null

export function setUnauthorizedHandler(fn: () => void) {
  unauthorizedHandler = fn
}

export async function apiRequest<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const isFormData = options.body instanceof FormData
  const headers: Record<string, string> = {
    ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
    ...(options.headers as Record<string, string> | undefined),
  }

  const resp = await fetch(path, {
    credentials: 'include',
    ...options,
    headers,
  })

  if (resp.status === 401) {
    if (unauthorizedHandler && !path.includes('/auth/login')) {
      unauthorizedHandler()
    }
    let detail = '请先登录'
    try {
      const data = await resp.json()
      if (data?.detail) detail = data.detail
    } catch {}
    throw new ApiError(detail, 401)
  }

  if (!resp.ok) {
    let detail = `请求失败 (HTTP ${resp.status})`
    try {
      const data = await resp.json()
      if (data?.detail) detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } catch {}
    throw new ApiError(detail, resp.status)
  }

  return resp.json() as Promise<T>
}

export const api = {
  me: () => apiRequest<{ authenticated: boolean; username: string; auth_type: string }>('/api/v1/auth/me'),
  login: (username: string, password: string) =>
    apiRequest('/api/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  logout: () => apiRequest('/api/v1/auth/logout', { method: 'POST' }),

  status: () => apiRequest('/api/v1/status'),
  qrStart: () => apiRequest('/api/v1/115/qr/start', { method: 'POST' }),
  qrStatus: (sessionId: string) => apiRequest(`/api/v1/115/qr/${encodeURIComponent(sessionId)}`),

  parseFile: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return apiRequest('/api/v1/files/parse', { method: 'POST', body: fd })
  },
  parseText: (raw_text: string) =>
    apiRequest('/api/v1/downloads/parse-text', {
      method: 'POST',
      body: JSON.stringify({ raw_text }),
    }),
  submitDownload: (payload: {
    links?: string[]
    raw_text?: string
    save_path?: string
    custom_folder_name?: string
    notify_tg?: boolean
    remember_path?: boolean
  }) =>
    apiRequest('/api/v1/downloads/submit', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  listTasks: (filter = 'all', limit = 100) =>
    apiRequest(`/api/v1/tasks?filter=${encodeURIComponent(filter)}&limit=${limit}`),
  getLiveTask: (taskId: string) => apiRequest(`/api/v1/tasks/live/${encodeURIComponent(taskId)}`),
  getGroupDetail: (groupId: string, page = 1, mode = 'all') =>
    apiRequest(`/api/v1/tasks/groups/${encodeURIComponent(groupId)}?page=${page}&mode=${mode}`),
  retryGroup: (groupId: string) =>
    apiRequest(`/api/v1/tasks/groups/${encodeURIComponent(groupId)}/retry`, { method: 'POST' }),
  deleteGroup: (groupId: string) =>
    apiRequest(`/api/v1/tasks/groups/${encodeURIComponent(groupId)}`, { method: 'DELETE' }),
  clearGroups: (category: 'done' | 'fail') =>
    apiRequest(`/api/v1/tasks/groups?category=${category}`, { method: 'DELETE' }),

  cloudTasks: (page = 1) => apiRequest(`/api/v1/cloud/tasks?page=${page}`),
  deleteCloudTask: (infoHash: string, deleteSource = false) =>
    apiRequest(`/api/v1/cloud/tasks/${encodeURIComponent(infoHash)}?delete_source=${deleteSource}`, {
      method: 'DELETE',
    }),
  clearCloudTasks: (flag: 1 | 2 | 4) =>
    apiRequest('/api/v1/cloud/tasks/clear', {
      method: 'POST',
      body: JSON.stringify({ flag }),
    }),

  getSettings: () => apiRequest('/api/v1/settings'),
  updateSettings: (payload: {
    auto_retry_enabled?: boolean
    retry_max_wait_hours?: number
    notify_tg_on_web_download?: boolean
    default_save_path?: string
  }) =>
    apiRequest('/api/v1/settings', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  resetDefaultPath: () => apiRequest('/api/v1/settings/reset-default-path', { method: 'POST' }),
  reloadConfig: () => apiRequest('/api/v1/settings/reload', { method: 'POST' }),
  changePassword: (current_password: string, new_password: string, new_username?: string) =>
    apiRequest('/api/v1/settings/password', {
      method: 'POST',
      body: JSON.stringify({ current_password, new_password, new_username }),
    }),
  rotateApiKey: () => apiRequest('/api/v1/settings/api-key/rotate', { method: 'POST' }),
  getLogs: (lines = 200) => apiRequest(`/api/v1/logs?lines=${lines}`),
}
