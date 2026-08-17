/**
 * axios 统一请求封装
 * - 请求自动携带 Authorization: Bearer <token>
 * - 统一解包响应 data
 * - 401 时清除凭证并广播 auth:unauthorized（App.vue 监听后跳转登录页）
 * - 网络/超时/服务端错误统一归一化为 ApiFailure，组件层无需散落 try/catch
 */
import axios, { AxiosError, type AxiosInstance, type AxiosRequestConfig } from 'axios'

/** 统一请求结果：成功携带 data，失败携带用户可读的 message */
export type ApiResult<T> = { ok: true; data: T } | { ok: false; message: string }

const TOKEN_KEY = 'token'

export const http: AxiosInstance = axios.create({
  timeout: 15_000,
  headers: { 'Content-Type': 'application/json' },
})

// 请求拦截：注入 token
http.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截：401 统一登出（与项目 auth:unauthorized 事件约定一致）
http.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      window.dispatchEvent(new CustomEvent('auth:unauthorized'))
    }
    return Promise.reject(error)
  },
)

/** 发起请求并解包 data；任何失败都返回 { ok: false, message } 而不抛异常 */
export async function request<T>(config: AxiosRequestConfig): Promise<ApiResult<T>> {
  try {
    const response = await http.request<T>(config)
    return { ok: true, data: response.data }
  } catch (error) {
    const axiosError = error as AxiosError<{ detail?: string; message?: string }>

    if (axiosError.response?.status === 401) {
      return { ok: false, message: '登录已过期，请重新登录' }
    }
    if (axiosError.code === 'ECONNABORTED') {
      return { ok: false, message: '请求超时，请检查网络后重试' }
    }
    if (!axiosError.response) {
      return { ok: false, message: '网络异常，请检查网络连接' }
    }

    const message =
      axiosError.response.data?.detail ??
      axiosError.response.data?.message ??
      `请求失败（${axiosError.response.status}）`
    return { ok: false, message }
  }
}
