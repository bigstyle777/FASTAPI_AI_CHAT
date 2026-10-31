/**
 * axios 统一请求封装
 * - 凭证走 httponly cookie（后端 set-cookie），浏览器自动携带，JS 无需也读不到 token
 * - withCredentials 让跨域请求也带 cookie（dev 经 vite proxy 同源，生产经 nginx 同源）
 * - 401 时广播 auth:unauthorized（App.vue 监听后跳转登录页）
 * - 网络/超时/服务端错误统一归一化为 ApiFailure，组件层无需散落 try/catch
 */
import axios, { AxiosError, type AxiosInstance, type AxiosRequestConfig } from 'axios'

/** 统一请求结果：成功携带 data，失败携带用户可读的 message */
export type ApiResult<T> = { ok: true; data: T } | { ok: false; message: string }

export const http: AxiosInstance = axios.create({
  timeout: 15_000,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
})

// 401 统一登出（凭证在 httponly cookie 里，前端清不掉，只能让后端 logout；
// 这里只广播事件让 UI 跳转登录页）
http.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
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
