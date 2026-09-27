// vitest jsdom 全局 stub（PwaInstallPrompt 测试用）
// @ts-nocheck
import { vi, beforeEach } from 'vitest'

// matchMedia 在 jsdom 不存在 → stub（默认 coarse=false 桌面 / standalone=false）
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

// jsdom 默认 localStorage 可能缺失/未实现 → 显式 stub
const store = new Map<string, string>()
Object.defineProperty(window, 'localStorage', {
  writable: true,
  value: {
    getItem: vi.fn((k: string) => store.get(k) ?? null),
    setItem: vi.fn((k: string, v: string) => void store.set(k, String(v))),
    removeItem: vi.fn((k: string) => void store.delete(k)),
    clear: vi.fn(() => store.clear()),
  },
})
if (typeof globalThis.localStorage === 'undefined') {
  Object.defineProperty(globalThis, 'localStorage', {
    writable: true,
    value: window.localStorage,
  })
}

// 每次测试前清空 localStorage（各用例独立）
beforeEach(() => {
  store.clear()
})