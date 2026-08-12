import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import markdownItKatex from '@traptitech/markdown-it-katex'
import { ref } from 'vue'
import { useThemeStore } from '@/stores/theme'

type MarkdownItInstance = InstanceType<typeof MarkdownIt>

let mdInstance: MarkdownItInstance | null = null
let lightMdInstance: MarkdownItInstance | null = null
let mermaidInitialized = false

function buildMarkdownInstance(useHighlight: boolean): MarkdownItInstance {
  const md: MarkdownItInstance = new MarkdownIt({
    html: false,
    breaks: true,
    linkify: true,
    highlight(code: string, lang: string): string {
      // 流式渲染时跳过 highlight.js：自动语言检测（highlightAuto）在长代码块上
      // 会阻塞主线程，导致页面长时间不重绘、回复"卡住后一次性蹦出"
      if (!useHighlight) return ''
      const language = (lang || '').toLowerCase()
      if (language === 'mermaid') {
        return `<pre class="mermaid-pre"><code class="language-mermaid">${md.utils.escapeHtml(code)}</code></pre>`
      }
      if (language && hljs.getLanguage(language)) {
        try {
          return `<pre class="hljs"><code class="hljs language-${language}">${hljs.highlight(code, { language }).value}</code></pre>`
        } catch {
          // fall through
        }
      }
      try {
        return `<pre class="hljs"><code class="hljs">${hljs.highlightAuto(code).value}</code></pre>`
      } catch {
        return `<pre class="hljs"><code>${md.utils.escapeHtml(code)}</code></pre>`
      }
    },
  })

  md.use(markdownItKatex)
  return md
}

function getMarkdownInstance(): MarkdownItInstance | null {
  if (mdInstance) return mdInstance
  mdInstance = buildMarkdownInstance(true)
  return mdInstance
}

function getLightMarkdownInstance(): MarkdownItInstance | null {
  if (lightMdInstance) return lightMdInstance
  lightMdInstance = buildMarkdownInstance(false)
  return lightMdInstance
}

/** 在容器内渲染完整 markdown（非流式） */
export function renderMarkdown(text: string): string {
  const md = getMarkdownInstance()
  if (!md || !text) return text || ''
  try {
    return md.render(text)
  } catch {
    return text
  }
}

/** 流式渲染：找出安全边界，渲染稳定部分，尾部纯文本显示 */
export function renderStreamingMarkdown(text: string): { stable: string; tail: string } {
  if (!text) return { stable: '', tail: '' }
  const boundary = findStreamingBoundary(text)
  return {
    stable: text.slice(0, boundary),
    tail: text.slice(boundary),
  }
}

function findUnclosedFenceStart(text: string): number {
  const fencePattern = /^([`~]{3,})(.*)$/
  const lines = text.split(/(\n)/)
  let offset = 0
  let openFence: { char: string; length: number; start: number } | null = null

  for (let i = 0; i < lines.length; i += 2) {
    const line = lines[i] || ''
    const newline = lines[i + 1] || ''
    const match = line.match(fencePattern)
    if (match) {
      const marker = match[1] || ''
      const fence = { char: marker[0] || '', length: marker.length, start: offset }
      if (!openFence) {
        openFence = fence
      } else if (fence.char === openFence.char && fence.length >= openFence.length) {
        openFence = null
      }
    }
    offset += line.length + newline.length
  }
  return openFence ? openFence.start : -1
}

function findUnclosedInlineCodeStart(text: string, limit: number): number {
  let openIndex = -1
  for (let i = 0; i < limit; i++) {
    if (text[i] !== '`') continue
    if (i > 0 && text[i - 1] === '\\') continue
    let end = i + 1
    while (end < limit && text[end] === '`') end++
    if (openIndex === -1) {
      openIndex = i
    } else {
      openIndex = -1
    }
    i = end - 1
  }
  return openIndex
}

function findUnclosedBlockMathStart(text: string, limit: number): number {
  let openIndex = -1
  for (let i = 0; i < limit - 1; i++) {
    if (text[i] !== '$' || text[i + 1] !== '$') continue
    if (i > 0 && text[i - 1] === '\\') continue
    openIndex = openIndex === -1 ? i : -1
    i++
  }
  return openIndex
}

function findUnclosedInlineMathStart(text: string, limit: number): number {
  let openIndex = -1
  for (let i = 0; i < limit; i++) {
    if (text[i] !== '$') continue
    if (i > 0 && text[i - 1] === '\\') continue
    if (text[i + 1] === '$' || text[i - 1] === '$') continue
    openIndex = openIndex === -1 ? i : -1
  }
  return openIndex
}

const SENTENCE_END_CHARS = '。！？!?…；;.'

function findLastSentenceEnd(text: string, limit: number): number {
  const end = Math.min(limit, text.length)
  for (let i = end - 1; i >= 0; i--) {
    if (SENTENCE_END_CHARS.includes(text.charAt(i))) {
      return i + 1
    }
  }
  return -1
}

/** 判断在 cut 位置截断是否安全：不会把未闭合的代码块/行内代码/公式切成两半 */
function isSafeStreamingCut(text: string, cut: number): boolean {
  let fenceOpen = false
  for (const line of text.slice(0, cut).split('\n')) {
    if (/^[`~]{3,}/.test(line)) fenceOpen = !fenceOpen
  }
  if (fenceOpen) return false
  if (findUnclosedInlineCodeStart(text, cut) >= 0) return false
  if (findUnclosedBlockMathStart(text, cut) >= 0) return false
  if (findUnclosedInlineMathStart(text, cut) >= 0) return false
  return true
}

function findStreamingBoundary(text: string): number {
  if (!text) return 0
  let safeLimit = text.length

  const fenceStart = findUnclosedFenceStart(text)
  if (fenceStart >= 0) safeLimit = Math.min(safeLimit, fenceStart)

  const codeStart = findUnclosedInlineCodeStart(text, safeLimit)
  if (codeStart >= 0) safeLimit = Math.min(safeLimit, codeStart)

  const mathStart = findUnclosedBlockMathStart(text, safeLimit)
  if (mathStart >= 0) safeLimit = Math.min(safeLimit, mathStart)

  const inlineMathStart = findUnclosedInlineMathStart(text, safeLimit)
  if (inlineMathStart >= 0) safeLimit = Math.min(safeLimit, inlineMathStart)

  const candidates: number[] = []

  const blockBoundary = text.lastIndexOf('\n\n', safeLimit)
  if (blockBoundary >= 0) candidates.push(blockBoundary + 2)

  const sentenceCut = findLastSentenceEnd(text, safeLimit)
  if (sentenceCut > 0) candidates.push(sentenceCut)

  const lineBoundary = text.lastIndexOf('\n', safeLimit)
  if (lineBoundary >= 80) candidates.push(lineBoundary + 1)

  for (const cut of candidates) {
    if (isSafeStreamingCut(text, cut)) return cut
  }

  // 兜底：保留一段尾部窗口作为纯文本 tail，避免整段 markdown 逐 token 重渲染
  return Math.max(0, safeLimit - 60)
}

/** 在 DOM 容器上渲染完整 markdown，并处理 mermaid 和代码复制按钮 */
export async function renderMarkdownToContainer(
  container: HTMLElement,
  text: string,
): Promise<void> {
  const html = renderMarkdown(text)
  container.innerHTML = html
  container.classList.add('markdown-body')
  await renderMermaidBlocks(container)
  addCodeCopyButtons(container)
}

/** 在 DOM 容器上渲染流式 markdown */
export function renderStreamingMarkdownToContainer(
  container: HTMLElement,
  text: string,
  stableTracker: { value: string },
  canRenderStable: () => boolean = () => true,
): void {
  const md = getLightMarkdownInstance()
  if (!md || !text) {
    container.textContent = text || ''
    return
  }

  const { stable, tail } = renderStreamingMarkdown(text)

  try {
    if (stable && stableTracker.value !== stable && canRenderStable()) {
      container.innerHTML = md.render(stable)
      container.classList.add('markdown-body')
      stableTracker.value = stable
    } else if (!stable) {
      container.textContent = ''
      container.classList.add('markdown-body')
      stableTracker.value = ''
    }

    let tailEl = container.querySelector('.markdown-stream-tail') as HTMLElement | null
    if (tail) {
      if (!tailEl) {
        tailEl = document.createElement('span')
        tailEl.className = 'markdown-stream-tail'
        container.appendChild(tailEl)
      }
      tailEl.textContent = tail
    } else if (tailEl) {
      tailEl.remove()
    }
  } catch {
    container.textContent = text
  }
}

function addCodeCopyButtons(container: HTMLElement): void {
  container.querySelectorAll('pre').forEach((pre) => {
    if (pre.classList.contains('mermaid-pre')) return
    if (pre.querySelector('.code-copy-btn')) return
    const code = pre.querySelector('code')
    if (!code) return
    pre.classList.add('code-block')
    const btn = document.createElement('button')
    btn.type = 'button'
    btn.className = 'code-copy-btn'
    btn.textContent = '复制'
    btn.title = '复制代码'
    btn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(code.textContent || '')
        btn.textContent = '已复制'
      } catch {
        btn.textContent = '失败'
      }
      setTimeout(() => {
        btn.textContent = '复制'
      }, 1500)
    })
    pre.appendChild(btn)
  })
}

async function renderMermaidBlocks(container: HTMLElement): Promise<void> {
  const codeBlocks = Array.from(
    container.querySelectorAll('pre.mermaid-pre code.language-mermaid'),
  )
  if (codeBlocks.length === 0) return

  const mermaid = await import('mermaid')
  const themeStore = useThemeStore()
  if (!mermaidInitialized) {
    mermaid.default.initialize({
      startOnLoad: false,
      theme: themeStore.resolved === 'dark' ? 'dark' : 'default',
      securityLevel: 'loose',
    })
    mermaidInitialized = true
  }

  for (let i = 0; i < codeBlocks.length; i++) {
    const codeEl = codeBlocks[i] as HTMLElement
    const pre = codeEl.parentElement!
    const graphDef = codeEl.textContent || ''
    const wrapper = document.createElement('div')
    wrapper.className = 'mermaid-wrapper'
    const target = document.createElement('div')
    wrapper.appendChild(target)
    pre.replaceWith(wrapper)
    try {
      const id = `mermaid-${Date.now()}-${i}`
      const { svg } = await mermaid.default.render(id, graphDef)
      target.innerHTML = svg
    } catch (err) {
      target.textContent = '流程图渲染失败: ' + ((err as Error).message || err)
    }
  }
}

/** composable: 提供响应式的 markdown 渲染状态 */
export function useMarkdown() {
  const stableTracker = ref('')
  let lastStableRender = 0
  const STABLE_RENDER_INTERVAL = 120

  async function renderFull(container: HTMLElement | null, text: string) {
    if (!container) return
    stableTracker.value = ''
    await renderMarkdownToContainer(container, text)
  }

  function renderStream(container: HTMLElement | null, text: string) {
    if (!container) return
    renderStreamingMarkdownToContainer(container, text, stableTracker, () => {
      const now = Date.now()
      if (now - lastStableRender >= STABLE_RENDER_INTERVAL) {
        lastStableRender = now
        return true
      }
      return false
    })
  }

  function resetStream() {
    stableTracker.value = ''
    lastStableRender = 0
  }

  return {
    renderFull,
    renderStream,
    resetStream,
  }
}
