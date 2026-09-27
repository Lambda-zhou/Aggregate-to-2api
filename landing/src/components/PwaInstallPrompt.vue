<script setup>
/**
 * PwaInstallPrompt.vue — v21 PWA 成场安装提示（移动端）
 * 仅当浏览器支持成场安装且尚未安装时展示：
 *  - Chrome/Edge/Android：监听 beforeinstallprompt，非安装态 + 访问 ≥2 次展示可关闭横幅
 *  - iOS Safari：无 beforeinstallprompt，用 navigator.standalone 检测 + 静态「添加到主屏幕」提示
 * 桌面（pointer:fine）不展示。零依赖，纯 Vue 响应式。
 */
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'

const SHOW_KEY = 'pwa-install-seen'
const VISIT_KEY = 'pwa-install-visits'
const VISIT_THRESHOLD = 2

const deferredPrompt = ref(null)
const dismissed = ref(false)
const isStandalone = ref(false)
const isCoarse = ref(false)
const visitCount = ref(0)

const isIos = computed(() => {
  if (typeof window === 'undefined') return false
  return /iphone|ipad|ipod/i.test(navigator.userAgent) && !('MSStream' in window)
})

const show = computed(() => {
  if (dismissed.value || isStandalone.value || !isCoarse.value) return false
  // iOS：静态提示，无视 beforeinstallprompt
  if (isIos.value) return visitCount.value >= VISIT_THRESHOLD
  // Chrome/Edge/Android：等待 beforeinstallprompt 事件
  return deferredPrompt.value !== null && visitCount.value >= VISIT_THRESHOLD
})

const msg = computed(() => (isIos.value ? '添加到主屏幕：点「分享」→「添加到主屏幕」' : '安装听风AI，获得应用般的使用体验'))

function install() {
  const p = deferredPrompt.value
  if (!p) return
  p.prompt()
  p.userChoice.then(() => {
    dismissed.value = true
    localStorage.setItem(SHOW_KEY, '1')
  })
}

function close() {
  dismissed.value = true
  localStorage.setItem(SHOW_KEY, '1')
}

function onBeforeInstall(e) {
  // 已拒绝过则不再打扰
  if (localStorage.getItem(SHOW_KEY)) return
  e.preventDefault()
  deferredPrompt.value = e
}

function onStandalone() {
  isStandalone.value =
    window.matchMedia('(display-mode: standalone)').matches ||
    (window.navigator.standalone === true)
}

function bumpVisit() {
  const n = Number(localStorage.getItem(VISIT_KEY) || 0) + 1
  localStorage.setItem(VISIT_KEY, String(n))
  visitCount.value = n
}

// 具名 handler：安装成功后置 dismissed 标记；与 onBeforeUnmount 成对移除（防监听器泄漏）
function onInstalled() {
  localStorage.setItem(SHOW_KEY, '1')
}

onMounted(() => {
  if (typeof window === 'undefined') return
  bumpVisit()
  isCoarse.value = window.matchMedia('(pointer: coarse)').matches || window.innerWidth < 768
  onStandalone()
  window.addEventListener('beforeinstallprompt', onBeforeInstall)
  window.addEventListener('appinstalled', onInstalled)
  window.addEventListener('resize', onStandalone)
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeinstallprompt', onBeforeInstall)
  window.removeEventListener('appinstalled', onInstalled)
  window.removeEventListener('resize', onStandalone)
})
</script>

<template>
  <Transition name="pwa-fade">
    <div v-if="show" class="pwa-install" role="region" aria-label="安装提示">
      <div class="pwa-install__text">{{ msg }}</div>
      <div class="pwa-install__actions">
        <button v-if="!isIos" class="btn btn-sm btn-primary" @click="install">安装</button>
        <button class="btn btn-sm btn-ghost" @click="close" aria-label="关闭提示">✕</button>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.pwa-install {
  position: fixed;
  left: 50%;
  bottom: 16px;
  transform: translateX(-50%);
  z-index: 1200;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  max-width: min(92vw, 420px);
  border-radius: 14px;
  background: rgba(10, 14, 26, 0.92);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: #fff;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
}

.pwa-install__text {
  flex: 1;
  font-size: 13px;
  line-height: 1.4;
}

.pwa-install__actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.pwa-fade-enter-active,
.pwa-fade-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}

.pwa-fade-enter-from,
.pwa-fade-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(8px);
}
</style>
