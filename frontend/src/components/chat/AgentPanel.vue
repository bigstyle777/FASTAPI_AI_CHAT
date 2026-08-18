<script setup lang="ts">
import { ref, computed, reactive } from 'vue'
import type { AgentSSEEvent, AgentPlanStep, AgentToolEvent, AgentStepEvent } from '@/types'
import { useChatStore } from '@/stores/chat'

const chatStore = useChatStore()

const planExpanded = ref(true)
const stepExpanded = reactive<Record<number, boolean>>({})
const toolExpanded = reactive<Record<string, boolean>>({})

// 从 agentEvents 中提取结构化数据
const plan = computed<AgentPlanStep[]>(() => {
  const planEvent = chatStore.agentEvents.find((e) => e.type === 'agent_plan')
  if (!planEvent || planEvent.type !== 'agent_plan') return []
  return (planEvent as { steps: AgentPlanStep[] }).steps ?? []
})

// 步骤状态映射 { stepIndex: { status, output, error, tools: AgentToolEvent[] } }
const stepState = computed(() => {
  const map: Record<number, {
    status: string
    output: string | null
    error: string | null
    tools: AgentToolEvent[]
  }> = {}

  for (const event of chatStore.agentEvents) {
    if (event.type === 'agent_step') {
      const e = event as AgentStepEvent
      if (!map[e.index]) {
        map[e.index] = { status: 'pending', output: null, error: null, tools: [] }
      }
      const s = map[e.index] as NonNullable<(typeof map)[number]>
      s.status = e.status
      s.output = e.output ?? null
      s.error = e.error ?? null
    }
    if (event.type === 'agent_tool') {
      const e = event as AgentToolEvent
      if (!map[e.step_index]) {
        map[e.step_index] = { status: 'pending', output: null, error: null, tools: [] }
      }
      const t = map[e.step_index] as NonNullable<(typeof map)[number]>
      t.tools.push(e)
    }
  }

  return map
})

// 当前正在执行的步骤索引
const activeStepIndex = computed(() => {
  for (const [idx, state] of Object.entries(stepState.value)) {
    if (state.status === 'started') return Number(idx)
  }
  return null
})

// 运行状态
const runStatus = computed(() => {
  const doneEvent = chatStore.agentEvents.find((e) => e.type === 'done')
  if (!doneEvent || doneEvent.type !== 'done') return 'running'
  return doneEvent.status
})

// 步骤数量
const stepCount = computed(() => plan.value.length || Object.keys(stepState.value).length || 0)

// 完成步骤数
const completedSteps = computed(() =>
  Object.values(stepState.value).filter((s) => s.status === 'completed').length
)

// 状态图标
function statusIcon(status: string): string {
  switch (status) {
    case 'completed': return 'check'
    case 'failed': return 'x'
    case 'started': return 'loader'
    case 'skipped': return 'skip'
    default: return 'dot'
  }
}

// 状态文本
function statusText(status: string): string {
  switch (status) {
    case 'completed': return '完成'
    case 'failed': return '失败'
    case 'started': return '执行中…'
    case 'skipped': return '已跳过'
    default: return '等待中'
  }
}

// 工具名称美化
function toolLabel(name: string): string {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

// 格式化参数
function formatArgs(args: Record<string, unknown> | null | undefined): string {
  if (!args) return ''
  const entries = Object.entries(args)
  if (entries.length === 0) return ''
  return entries
    .map(([k, v]) => {
      const val = typeof v === 'string' ? v : JSON.stringify(v)
      return `${k}: ${val.length > 80 ? val.slice(0, 80) + '…' : val}`
    })
    .join('\n')
}

// 格式化结果
function formatResult(result: unknown): string {
  if (result === null || result === undefined) return ''
  if (typeof result === 'string') return result
  return JSON.stringify(result, null, 2)
}

function toggleStep(index: number) {
  stepExpanded[index] = !stepExpanded[index]
}

function toggleTool(key: string) {
  toolExpanded[key] = !toolExpanded[key]
}

function toolKey(stepIndex: number, toolIndex: number): string {
  return `${stepIndex}-${toolIndex}`
}
</script>

<template>
  <div class="agent-panel">
    <!-- 面板头部 -->
    <div class="agent-panel-header">
      <div class="agent-panel-title-row">
        <span class="agent-panel-icon">&#9881;</span>
        <span class="agent-panel-title">Agent 任务执行</span>
        <span v-if="runStatus === 'running'" class="agent-panel-badge running">运行中</span>
        <span v-else-if="runStatus === 'completed'" class="agent-panel-badge completed">已完成</span>
        <span v-else-if="runStatus === 'stopped'" class="agent-panel-badge stopped">已停止</span>
        <span v-else class="agent-panel-badge failed">失败</span>
      </div>
      <span v-if="stepCount > 0" class="agent-panel-progress">
        {{ completedSteps }} / {{ stepCount }}
      </span>
    </div>

    <!-- 计划列表 -->
    <div v-if="plan.length > 0" class="agent-plan">
      <button
        type="button"
        class="agent-plan-toggle"
        @click="planExpanded = !planExpanded"
      >
        <span :class="['agent-plan-arrow', { expanded: planExpanded }]">&#9654;</span>
        <span class="agent-plan-label">执行计划（{{ plan.length }} 步）</span>
      </button>

      <div v-show="planExpanded" class="agent-plan-steps">
        <div
          v-for="(step, idx) in plan"
          :key="idx"
          :class="[
            'agent-step-item',
            stepState[idx]?.status ?? 'pending',
            { active: idx === activeStepIndex },
          ]"
        >
          <!-- 步骤头部 -->
          <div class="agent-step-header" @click="toggleStep(idx)">
            <!-- 状态指示器 -->
            <span :class="['agent-step-status', stepState[idx]?.status ?? 'pending']">
              <!-- 执行中：旋转齿轮 -->
              <span v-if="stepState[idx]?.status === 'started'" class="agent-status-icon spinner">&#9881;</span>
              <!-- 完成：勾 -->
              <span v-else-if="stepState[idx]?.status === 'completed'" class="agent-status-icon check">&#10003;</span>
              <!-- 失败：叉 -->
              <span v-else-if="stepState[idx]?.status === 'failed'" class="agent-status-icon fail">&#10007;</span>
              <!-- 跳过 -->
              <span v-else-if="stepState[idx]?.status === 'skipped'" class="agent-status-icon skip">&#8594;</span>
              <!-- 等待 -->
              <span v-else class="agent-status-icon pending">&#9679;</span>
            </span>

            <!-- 步骤信息 -->
            <div class="agent-step-info">
              <span class="agent-step-desc">{{ idx + 1 }}. {{ step.description }}</span>
              <span v-if="step.tool" class="agent-step-tool-badge">{{ step.tool }}</span>
            </div>

            <span class="agent-step-expand">&#9660;</span>
          </div>

          <!-- 步骤详情 -->
          <div v-show="stepExpanded[idx]" class="agent-step-detail">
            <!-- 错误信息 -->
            <div v-if="stepState[idx]?.error" class="agent-step-error">
              {{ stepState[idx]?.error }}
            </div>

            <!-- 输出 -->
            <div v-if="stepState[idx]?.output" class="agent-step-output">
              <div class="agent-output-label">输出</div>
              <pre class="agent-output-text">{{ stepState[idx]?.output }}</pre>
            </div>

            <!-- 工具调用 -->
            <div v-if="stepState[idx]?.tools?.length" class="agent-step-tools">
              <div
                v-for="(tool, tIdx) in stepState[idx].tools"
                :key="tIdx"
                :class="['agent-tool-item', tool.status]"
              >
                <button
                  type="button"
                  class="agent-tool-header"
                  @click="toggleTool(toolKey(idx, tIdx))"
                >
                  <span :class="['agent-tool-status', tool.status]">
                    <span v-if="tool.status === 'started'" class="agent-status-icon spinner">&#9881;</span>
                    <span v-else-if="tool.status === 'completed'" class="agent-status-icon check">&#10003;</span>
                    <span v-else class="agent-status-icon fail">&#10007;</span>
                  </span>
                  <span class="agent-tool-name">{{ toolLabel(tool.tool) }}</span>
                  <span v-if="tool.duration_ms" class="agent-tool-duration">{{ tool.duration_ms }}ms</span>
                  <span class="agent-tool-expand">&#9660;</span>
                </button>

                <div v-show="toolExpanded[toolKey(idx, tIdx)]" class="agent-tool-detail">
                  <div v-if="tool.arguments && Object.keys(tool.arguments).length > 0" class="agent-tool-section">
                    <div class="agent-tool-section-label">参数</div>
                    <pre class="agent-tool-code">{{ formatArgs(tool.arguments) }}</pre>
                  </div>
                  <div v-if="tool.result !== undefined && tool.result !== null" class="agent-tool-section">
                    <div class="agent-tool-section-label">结果</div>
                    <pre class="agent-tool-code">{{ formatResult(tool.result) }}</pre>
                  </div>
                  <div v-if="tool.error" class="agent-tool-error">
                    <div class="agent-tool-section-label">错误</div>
                    <pre class="agent-tool-code error">{{ tool.error }}</pre>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 没有计划但有步骤事件 -->
    <div v-else-if="Object.keys(stepState).length > 0" class="agent-plan">
      <div class="agent-plan-label" style="padding: 0 0 8px;">执行中…</div>
    </div>
  </div>
</template>