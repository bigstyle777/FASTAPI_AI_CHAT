import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import markdownItKatex from '@traptitech/markdown-it-katex'
import { ref } from 'vue'
import { useThemeStore } from '@/stores/theme'

type MarkdownItInstance = InstanceType<typeof MarkdownIt>

let mdInstance: MarkdownItInstance | null = null
let mermaidInitialized = false

function getMarkdownInstance(): MarkdownItInstance | null {
  if (mdInstance) return mdInstance

  mdInstance = new MarkdownIt({
    html: false,
    breaks: true,
    linkify: true,
    highlight(code, lang) {
      const language = (lang || '').toLowerCase()
      if (language === 'mermaid') {
        return `<pre class="mermaid-pre"><code class="language-mermaid">${mdInstance!.utils.escapeHtml(code)}</code></pre>`
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
        return `<pre class="hljs"><code>${mdInstance!.utils.escapeHtml(code)}</code></pre>`
      }
    },
  })

  mdInstance.use(markdownItKatex)
  return mdInstance
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

  const blockBoundary = text.lastIndexOf('\n\n', safeLimit)
  if (blockBoundary >= 0) return blockBoundary + 2

  const lineBoundary = text.lastIndexOf('\n', safeLimit)
  if (lineBoundary >= 80) return lineBoundary + 1

  return safeLimit
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
): void {
  const md = getMarkdownInstance()
  if (!md || !text) {
    container.textContent = text || ''
    return
  }

  const { stable, tail } = renderStreamingMarkdown(text)

  try {
    if (stable && stableTracker.value !== stable) {
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

  async function renderFull(container: HTMLElement | null, text: string) {
    if (!container) return
    stableTracker.value = ''
    await renderMarkdownToContainer(container, text)
  }

  function renderStream(container: HTMLElement | null, text: string) {
    if (!container) return
    renderStreamingMarkdownToContainer(container, text, stableTracker)
  }

  function resetStream() {
    stableTracker.value = ''
  }

  return {
    renderFull,
    renderStream,
    resetStream,
  }
}
