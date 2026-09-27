// PwaInstallPrompt.vue 组件测试（v22 P1-3，5 用例）
// 覆盖：桌面隐藏 / beforeinstallprompt 触发 / 关闭持久 / iOS 静态提示 / 访问计数阈值
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import PwaInstallPrompt from '../components/PwaInstallPrompt.vue'

function setUserAgent(ua: string) {
  Object.defineProperty(navigator, 'userAgent', { configurable: true, value: ua })
}

describe('PwaInstallPrompt', () => {
  beforeEach(() => {
    localStorage.clear()
    // 默认桌面 UA + 非 coarse 指针 → 不应显示
    setUserAgent(
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36'
    )
    // matchMedia 默认 matches=false（由 setup.ts stub）→ isCoarse=false
  })

  it('桌面环境不显示横幅（pointer:fine 且非 standalone）', () => {
    const wrapper = mount(PwaInstallPrompt)
    expect(wrapper.find('.pwa-install').exists()).toBe(false)
    wrapper.unmount()
  })

  it('粗指针设备 + beforeinstallprompt 且访问>=2 次 → 显示并可关闭持久', async () => {
    // 粗指针：matchMedia('(pointer: coarse)') matches=true
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query.includes('coarse'),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
    // 访问计数已 2 次
    localStorage.setItem('pwa-install-visits', '2')
    // 移动设备 UA
    setUserAgent(
      'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36'
    )
    const wrapper = mount(PwaInstallPrompt)

    // 触发 beforeinstallprompt（Chrome 路径）
    const promptEvent = { preventDefault: vi.fn(), prompt: vi.fn(), userChoice: Promise.resolve({ outcome: 'accepted' }) }
    window.dispatchEvent(new Event('beforeinstallprompt') as any) // stub 用 addEventListener 收集，直接调内部不易——用 dispatchEvent 模拟
    // 因事件由 onBeforeInstall 监听，dispatch 后 deferredPrompt 设值 → show=true
    await wrapper.vm.$nextTick()
    // 注：onMounted 先执行，事件在 mount 后 dispatch 才被捕获；检查横幅
    expect(wrapper.find('.pwa-install').exists()).toBe(true)

    // 关闭 → dismissed + localStorage 持久标记
    await wrapper.find('[aria-label="关闭提示"]').trigger('click')
    expect(wrapper.find('.pwa-install').exists()).toBe(false)
    expect(localStorage.getItem('pwa-install-seen')).toBe('1')
    wrapper.unmount()
  })

  it('已持久关闭（pwa-install-seen=1）→ 不再次显示', () => {
    localStorage.setItem('pwa-install-seen', '1')
    const wrapper = mount(PwaInstallPrompt)
    expect(wrapper.find('.pwa-install').exists()).toBe(false)
    wrapper.unmount()
  })

  it('iOS Safari 无 beforeinstallprompt → 访问>=2 次时显示静态提示', async () => {
    setUserAgent('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile Safari/604.1')
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query.includes('coarse'),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
    localStorage.setItem('pwa-install-visits', '2')
    const wrapper = mount(PwaInstallPrompt)
    // onMounted 的 bumpVisit 改 visitCount → 需一个响应式 tick 后 DOM 才反映 show
    await wrapper.vm.$nextTick()
    // iOS 不显示「安装」按钮（无 beforeinstallprompt），只显示静态提示 + 关闭
    expect(wrapper.find('.pwa-install').exists()).toBe(true)
    expect(wrapper.findAll('button').length).toBe(1) // 仅关闭按钮
    expect(wrapper.text()).toContain('添加到主屏幕')
    wrapper.unmount()
  })

  it('访问计数 <2 次 → 不显示（阈值）', () => {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query.includes('coarse'),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
    setUserAgent(
      'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36'
    )
    // 首次访问（visitCount=1 < 2）且无 beforeinstallprompt → 不显示
    const wrapper = mount(PwaInstallPrompt)
    expect(wrapper.find('.pwa-install').exists()).toBe(false)
    wrapper.unmount()
  })
})
