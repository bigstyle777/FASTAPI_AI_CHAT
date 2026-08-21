import { apiJson } from './client'
import type {
  ActionResponse,
  AdminDashboard,
  AdminPermission,
  AdminRole,
  AdminUser,
  RoleResponse,
} from '@/types'

export function fetchAdminDashboard(): Promise<AdminDashboard | null> {
  return apiJson<AdminDashboard>('/admin/dashboard')
}

export function fetchAdminUsers(): Promise<AdminUser[] | null> {
  return apiJson<AdminUser[]>('/admin/users')
}

export function fetchAdminRoles(): Promise<AdminRole[] | null> {
  return apiJson<AdminRole[]>('/admin/roles')
}

export function fetchAdminPermissions(): Promise<AdminPermission[] | null> {
  return apiJson<AdminPermission[]>('/admin/permissions')
}

export function updateUserRole(
  userId: number,
  roleId: number,
): Promise<ActionResponse | null> {
  return apiJson<ActionResponse>(`/admin/users/${userId}/role`, {
    method: 'PATCH',
    body: JSON.stringify({ role_id: roleId }),
  })
}

export function createRole(
  name: string,
  description: string | null,
): Promise<RoleResponse | null> {
  return apiJson<RoleResponse>('/admin/roles', {
    method: 'POST',
    body: JSON.stringify({ name, description }),
  })
}

export function updateRolePermissions(
  roleId: number,
  permissionCodes: string[],
): Promise<ActionResponse | null> {
  return apiJson<ActionResponse>(`/admin/roles/${roleId}/permissions`, {
    method: 'PUT',
    body: JSON.stringify({ permission_codes: permissionCodes }),
  })
}
