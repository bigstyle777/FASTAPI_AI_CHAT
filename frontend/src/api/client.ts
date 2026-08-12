const API_BASE_URL = ''

export function getToken(): string | null {
  return localStorage.getItem('token')
}

export function setToken(token: string): void {
  localStorage.setItem('token', token)
}

export function removeToken(): void {
  localStorage.removeItem('token')
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
  const { auth = true, handleUnauthorized = true } = requestOptions
  // 默认 JSON；但当 body 是 FormData 时，必须由浏览器自动设置
  // multipart/form-data 及其 boundary，若强制 application/json 会导致后端 422
  const isFormData = options.body instanceof FormData
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  }
  if (!isFormData && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }

  if (auth) {
    const token = getToken()
    if (token) {
      headers.Authorization = `Bearer ${token}`
    }
  }

  try {
    const response = await fetch(`${API_BASE_URL}${url}`, {
      ...options,
      headers,
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
