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
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
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
