import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** 合并 tailwind class 并去重（shadcn-vue 标准工具函数） */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
