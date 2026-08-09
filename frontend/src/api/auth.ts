import { apiCall, apiJson } from './client'
import type {
  CaptchaResponse,
  LoginResponse,
  RegisterResponse,
  UserProfile,
  UserSettings,
} from '@/types'

export function fetchCaptcha(): Promise<CaptchaResponse | null> {
  return apiJson<CaptchaResponse>(
    '/users/captcha',
    { method: 'POST' },
    { auth: false, handleUnauthorized: false },
  )
}

export function loginUser(
  username: string,
  password: string,
  captchaId: string,
  captchaCode: string,
): Promise<LoginResponse | null> {
  return apiJson<LoginResponse>(
    '/users/login',
    {
      method: 'POST',
      body: JSON.stringify({
        username,
        password,
        captcha_id: captchaId,
        captcha_code: captchaCode,
      }),
    },
    { auth: false },
  )
}

export function registerUser(
  username: string,
  password: string,
): Promise<RegisterResponse | null> {
  return apiJson<RegisterResponse>(
    '/users/register',
    {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    },
    { auth: false },
  )
}

export function logoutUser(): Promise<void> {
  return apiCall('/users/logout', { method: 'POST' }, { handleUnauthorized: false })
    .then(() => undefined)
}

export function fetchUserProfile(): Promise<UserProfile | null> {
  return apiJson<UserProfile>('/users/me')
}

export function fetchUserSettings(): Promise<UserSettings | null> {
  return apiJson<UserSettings>('/users/settings')
}

export function saveUserSettings(
  apiKey: string,
  provider: string,
): Promise<UserSettings | null> {
  return apiJson<UserSettings>('/users/settings', {
    method: 'POST',
    body: JSON.stringify({ api_key: apiKey, provider }),
  })
}
