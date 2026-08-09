import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { AdminDashboard, AdminPermission, AdminRole, AdminUser } from '@/types'
import {
  createRole as apiCreateRole,
  fetchAdminDashboard,
  fetchAdminPermissions,
  fetchAdminRoles,
  fetchAdminUsers,
  updateRolePermissions as apiUpdateRolePermissions,
  updateUserRole as apiUpdateUserRole,
} from '@/api/admin'

export const useAdminStore = defineStore('admin', () => {
  const dashboard = ref<AdminDashboard['summary'] | null>(null)
  const users = ref<AdminUser[]>([])
  const roles = ref<AdminRole[]>([])
  const permissions = ref<AdminPermission[]>([])
  const selectedRoleId = ref<number>(0)
  const notice = ref<{ message: string; type: 'info' | 'success' | 'error' }>({ message: '', type: 'info' })

  function showNotice(message: string, type: 'info' | 'success' | 'error' = 'info') {
    notice.value = { message, type }
  }

  function clearNotice() {
    notice.value = { message: '', type: 'info' }
  }

  async function loadDashboard() {
    clearNotice()
    try {
      const [dashRes, usersRes, rolesRes, permsRes] = await Promise.all([
        fetchAdminDashboard(),
        fetchAdminUsers(),
        fetchAdminRoles(),
        fetchAdminPermissions(),
      ])

      dashboard.value = dashRes?.success ? dashRes.summary : null
      users.value = Array.isArray(usersRes) ? usersRes : []
      roles.value = Array.isArray(rolesRes) ? rolesRes : []
      permissions.value = Array.isArray(permsRes) ? permsRes : []

      if (roles.value.length > 0 && !selectedRoleId.value) {
        selectedRoleId.value = roles.value[0]?.role_id ?? 0
      }
    } catch (error) {
      console.error('loadDashboard failed:', error)
      showNotice('加载管理员数据失败', 'error')
    }
  }

  async function updateUserRole(userId: number, roleId: number) {
    const data = await apiUpdateUserRole(userId, roleId)
    if (!data?.success) {
      showNotice(data?.message || '更新用户角色失败', 'error')
      return
    }
    showNotice('用户角色已更新', 'success')
    await loadDashboard()
  }

  async function saveRolePermissions(roleId: number, permissionCodes: string[]) {
    const data = await apiUpdateRolePermissions(roleId, permissionCodes)
    if (!data?.success) {
      showNotice(data?.message || '更新角色权限失败', 'error')
      return
    }
    showNotice('角色权限已更新', 'success')
    await loadDashboard()
  }

  async function createNewRole(name: string, description: string | null) {
    if (!name.trim()) {
      showNotice('请输入角色名称', 'error')
      return
    }
    const data = await apiCreateRole(name.trim(), description || null)
    if (!data?.role_id) {
      showNotice('创建角色失败', 'error')
      return
    }
    showNotice('角色已创建', 'success')
    await loadDashboard()
    selectedRoleId.value = data.role_id
  }

  function selectRole(roleId: number) {
    selectedRoleId.value = roleId
  }

  function getSelectedRole(): AdminRole | undefined {
    return roles.value.find((r) => r.role_id === selectedRoleId.value)
  }

  return {
    dashboard,
    users,
    roles,
    permissions,
    selectedRoleId,
    notice,
    showNotice,
    clearNotice,
    loadDashboard,
    updateUserRole,
    saveRolePermissions,
    createNewRole,
    selectRole,
    getSelectedRole,
  }
})
