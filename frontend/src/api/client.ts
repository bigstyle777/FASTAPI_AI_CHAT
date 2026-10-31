/**
 * fetch 统一封装 + 登录态 UI 标志
 *
 * 凭证由后端 set-cookie（httponly），浏览器自动携带，JS 读不到也偷不走。
 * 这里请求只加 credentials:'include' 让 cookie 跟着走，不再手动注入
 * Authorization header。
 *
 * localStorage 只存一个 UI 标志 auth_flag（不是真 token），供路由守卫 /
 * Pinia 判断"是否已登录"做 UI 展示。XSS 即便偷到这个标志也无法冒充用户
 * —— 真凭证只在 httponly cookie 里。
 */
const API_BASE_URL = ''
const AUTH_FLAG = 'auth_flag'

/** 是否已登录（UI 用，不代表持有有效凭证——凭证在 httponly cookie 里） */
export function getToken(): string | null {
  return localStorage.getItem(AUTH_FLAG)
}

/** 登录成功后标记 UI 登录态（不存真 token） */
export function setToken(_token: string): void {
  localStorage.setItem(AUTH_FLAG, '1')
}

export function removeToken(): void {
  localStorage.removeItem(AUTH_FLAG)
}

interface RequestOptions {
  auth?: boolean
  handleUnauthorized?: boolean
  signal?: AbortSignal
}

export async function apiCall(
  url: string,
  options: RequestInit = {},
  requestOptions: RequestOptions = {},
): Promise<Response | null> {
  const { handleUnauthorized = true } = requestOptions
  // 默认 JSON；但当 body 是 FormData 时，必须由浏览器自动设置
  // multipart/form-data 及其 boundary，若强制 application/json 会导致后端 422
  const isFormData = options.body instanceof FormData
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  }
  if (!isFormData && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }

  try {
    // credentials:'include' 让 httponly cookie 自动带上，无需手动 Authorization
    const response = await fetch(`${API_BASE_URL}${url}`, {
      ...options,
      headers,
      credentials: 'include',
      signal: requestOptions.signal,
    })

    if (response.status === 401 && handleUnauthorized) {
      removeToken()
      window.dispatchEvent(new CustomEvent('auth:unauthorized'))
      return null
    }

    return response
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      return null
    }
    console.error('网络请求失败:', error)
    return null
  }
}

export async function apiJson<T>(
  url: string,
  options: RequestInit = {},
  requestOptions: RequestOptions = {},
): Promise<T | null> {
  const response = await apiCall(url, options, requestOptions)
  if (!response) return null
  return response.json() as Promise<T>
}
