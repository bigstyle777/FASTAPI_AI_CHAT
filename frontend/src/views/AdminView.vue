<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useAdminStore } from '@/stores/admin'

const router = useRouter()
const authStore = useAuthStore()
const adminStore = useAdminStore()

const newRoleName = ref('')
const newRoleDescription = ref('')
const selectedPermissionCodes = ref<Set<string>>(new Set())
// 每行用户角色选择的临时值
const userRoleSelections = ref<Record<number, number>>({})

const selectedRole = computed(() => adminStore.getSelectedRole())

function onRoleSelect() {
  const role = selectedRole.value
  if (role) {
    selectedPermissionCodes.value = new Set(
      (role.permissions || []).map((p) => p.code || (p as unknown as string)),
    )
  }
}

watch(() => adminStore.selectedRoleId, onRoleSelect, { immediate: true })

async function handleSavePermissions() {
  if (!adminStore.selectedRoleId) {
    adminStore.showNotice('请先选择一个角色', 'error')
    return
  }
  await adminStore.saveRolePermissions(
    adminStore.selectedRoleId,
    Array.from(selectedPermissionCodes.value),
  )
  onRoleSelect()
}

async function handleCreateRole() {
  await adminStore.createNewRole(newRoleName.value, newRoleDescription.value || null)
  newRoleName.value = ''
  newRoleDescription.value = ''
}

async function handleSaveUserRole(userId: number) {
  const roleId = userRoleSelections.value[userId]
  if (!roleId) {
    adminStore.showNotice('请选择角色', 'error')
    return
  }
  await adminStore.updateUserRole(userId, roleId)
}

function togglePermission(code: string) {
  if (selectedPermissionCodes.value.has(code)) {
    selectedPermissionCodes.value.delete(code)
  } else {
    selectedPermissionCodes.value.add(code)
  }
}

onMounted(async () => {
  if (!authStore.isLoggedIn) {
    router.push('/login')
    return
  }
  if (!authStore.user) {
    await authStore.loadProfile()
  }
  if (!authStore.isAdmin) {
    router.push('/chat')
    return
  }
  await adminStore.loadDashboard()
  onRoleSelect()
})
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="sidebar-header">
        <div class="brand-row">
          <span class="brand-mark small">AI</span>
          <div>
            <h2>AI Chat Pro</h2>
            <p>管理中心</p>
          </div>
        </div>
      </div>
      <div class="sidebar-footer">
        <button class="ghost-btn" type="button" @click="router.push('/chat')">返回聊天</button>
        <button class="ghost-btn" type="button" @click="router.push('/profile')">个人中心</button>
        <div class="sidebar-meta">
          <span>{{ authStore.user?.username || '--' }}</span>
          <span>{{ authStore.user?.role || '--' }}</span>
        </div>
      </div>
    </aside>

    <section class="admin-view">
      <header class="admin-header">
        <div>
          <h1>管理员中心</h1>
          <p>用户、角色、权限统一管理</p>
        </div>
        <div class="admin-actions">
          <button class="secondary-action" type="button" @click="adminStore.loadDashboard">刷新</button>
          <button class="secondary-action" type="button" @click="adminStore.loadDashboard">新增角色</button>
        </div>
      </header>

      <div v-if="adminStore.notice.message" :class="['notice', adminStore.notice.type]">
        {{ adminStore.notice.message }}
      </div>

      <!-- 统计 -->
      <div class="admin-stats">
        <div class="stat-box">
          <span class="stat-label">用户</span>
          <strong>{{ adminStore.dashboard?.users ?? adminStore.users.length }}</strong>
        </div>
        <div class="stat-box">
          <span class="stat-label">角色</span>
          <strong>{{ adminStore.dashboard?.roles ?? adminStore.roles.length }}</strong>
        </div>
        <div class="stat-box">
          <span class="stat-label">权限</span>
          <strong>{{ adminStore.dashboard?.permissions ?? adminStore.permissions.length }}</strong>
        </div>
        <div class="stat-box">
          <span class="stat-label">管理员</span>
          <strong>{{ adminStore.dashboard?.admin_users ?? 0 }}</strong>
        </div>
      </div>

      <!-- 用户列表 + 角色权限编辑 -->
      <div class="admin-grid">
        <section class="admin-panel">
          <div class="panel-head"><h2>用户列表</h2></div>
          <div class="table-wrap">
            <table class="admin-table">
              <thead>
                <tr>
                  <th>用户名</th>
                  <th>角色</th>
                  <th>权限</th>
                  <th>修改角色</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="user in adminStore.users" :key="user.user_id" :data-user-id="user.user_id">
                  <td>{{ user.username }}</td>
                  <td>{{ user.role }}</td>
                  <td>{{ (user.permissions || []).join(', ') || '--' }}</td>
                  <td>
                    <select
                      class="inline-select"
                      :value="userRoleSelections[user.user_id] ?? adminStore.roles.find(r => r.name === user.role)?.role_id"
                      @change="userRoleSelections[user.user_id] = Number(($event.target as HTMLSelectElement).value)"
                    >
                      <option v-for="role in adminStore.roles" :key="role.role_id" :value="role.role_id">
                        {{ role.name }}
                      </option>
                    </select>
                  </td>
                  <td>
                    <button
                      class="secondary-action"
                      type="button"
                      @click="handleSaveUserRole(user.user_id)"
                    >保存</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="admin-panel">
          <div class="panel-head"><h2>角色权限{{ selectedRole ? ' - ' + selectedRole.name : '' }}</h2></div>
          <div class="admin-form">
            <label class="field-label" for="roleNameInput">新增角色</label>
            <input id="roleNameInput" v-model="newRoleName" class="admin-input" type="text" placeholder="输入角色名称">
            <textarea v-model="newRoleDescription" class="admin-textarea" rows="2" placeholder="角色描述"></textarea>
            <button class="primary-action" type="button" @click="handleCreateRole">创建角色</button>
          </div>
          <div class="role-editor">
            <div class="role-select-row">
              <label class="field-label" for="rolePermissionSelect">编辑已有角色</label>
              <select
                id="rolePermissionSelect"
                class="admin-select"
                :value="adminStore.selectedRoleId"
                @change="adminStore.selectRole(Number(($event.target as HTMLSelectElement).value))"
              >
                <option v-for="role in adminStore.roles" :key="role.role_id" :value="role.role_id">
                  {{ role.name }}
                </option>
              </select>
            </div>
            <div class="permission-checklist">
              <label v-for="perm in adminStore.permissions" :key="perm.code" class="check-row">
                <input
                  type="checkbox"
                  :value="perm.code"
                  :checked="selectedPermissionCodes.has(perm.code)"
                  @change="togglePermission(perm.code)"
                >
                <span>
                  <strong>{{ perm.code }}</strong>
                  <em>{{ perm.name }}</em>
                </span>
              </label>
            </div>
            <button class="primary-action" type="button" @click="handleSavePermissions">保存权限</button>
          </div>
        </section>
      </div>

      <!-- 权限字典 -->
      <section class="admin-panel">
        <div class="panel-head"><h2>权限字典</h2></div>
        <div class="table-wrap">
          <table class="admin-table">
            <thead>
              <tr>
                <th>权限码</th>
                <th>名称</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="perm in adminStore.permissions" :key="perm.permission_id">
                <td>{{ perm.code }}</td>
                <td>{{ perm.name }}</td>
                <td>{{ perm.description || '--' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- 角色概览 -->
      <section class="admin-panel">
        <div class="panel-head"><h2>角色概览</h2></div>
        <div class="table-wrap">
          <table class="admin-table">
            <thead>
              <tr>
                <th>角色</th>
                <th>描述</th>
                <th>权限</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="role in adminStore.roles" :key="role.role_id" :data-role-id="role.role_id">
                <td>{{ role.name }}</td>
                <td>{{ role.description || '--' }}</td>
                <td>{{ (role.permissions || []).map((p) => p.code || p).join(', ') || '--' }}</td>
                <td>
                  <button class="secondary-action" type="button" @click="adminStore.selectRole(role.role_id)">编辑权限</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </section>
  </div>
</template>
