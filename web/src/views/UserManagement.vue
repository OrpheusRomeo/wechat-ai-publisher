<template>
  <div>
    <h2>👥 用户管理</h2>

    <div style="margin-bottom: 16px">
      <el-button type="primary" @click="showCreate = true">添加用户</el-button>
    </div>

    <el-table :data="users" stripe>
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="username" label="用户名" width="120" />
      <el-table-column prop="nickname" label="昵称" width="120" />
      <el-table-column prop="role" label="角色" width="100">
        <template #default="{ row }">
          <el-tag :type="row.role === 'superadmin' ? 'danger' : 'primary'" size="small">
            {{ row.role }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="is_active" label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
            {{ row.is_active ? '激活' : '禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="160">
        <template #default="{ row }">{{ row.created_at?.slice(0, 16) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="160">
        <template #default="{ row }">
          <el-button size="small" @click="editUser(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="deleteUser(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 创建/编辑弹窗 -->
    <el-dialog v-model="showCreate" :title="editing ? '编辑用户' : '添加用户'" width="480px">
      <el-form :model="userForm" label-width="80px">
        <el-form-item label="用户名" required>
          <el-input v-model="userForm.username" :disabled="!!editing" />
        </el-form-item>
        <el-form-item label="密码" :required="!editing">
          <el-input v-model="userForm.password" type="password" show-password
            :placeholder="editing ? '留空则不修改' : ''" />
        </el-form-item>
        <el-form-item label="昵称">
          <el-input v-model="userForm.nickname" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="userForm.role">
            <el-option label="管理员" value="admin" />
            <el-option label="超级管理员" value="superadmin" />
          </el-select>
        </el-form-item>
        <el-form-item label="激活">
          <el-switch v-model="userForm.is_active" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSaveUser">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { authApi } from '@/api/auth'
import { ElMessage, ElMessageBox } from 'element-plus'

const users = ref<any[]>([])
const showCreate = ref(false)
const editing = ref<any>(null)
const saving = ref(false)
const userForm = reactive({ username: '', password: '', nickname: '', role: 'admin', is_active: true })

async function load() {
  try { users.value = await authApi.listUsers() } catch { /* ignore */ }
}

function editUser(row: any) {
  editing.value = row
  userForm.username = row.username
  userForm.password = ''
  userForm.nickname = row.nickname || ''
  userForm.role = row.role
  userForm.is_active = row.is_active
  showCreate.value = true
}

async function handleSaveUser() {
  saving.value = true
  try {
    if (editing.value) {
      await authApi.updateUser(editing.value.id, {
        nickname: userForm.nickname,
        role: userForm.role,
        is_active: userForm.is_active,
        password: userForm.password || undefined,
      })
    } else {
      if (!userForm.username || !userForm.password) { ElMessage.warning('用户名和密码为必填'); return }
      await authApi.createUser({ ...userForm })
    }
    ElMessage.success('已保存')
    showCreate.value = false
    editing.value = null
    load()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally { saving.value = false }
}

async function deleteUser(row: any) {
  try {
    await ElMessageBox.confirm(`确定删除用户 ${row.username}?`, '确认删除', { type: 'warning' })
    await authApi.deleteUser(row.id)
    ElMessage.success('已删除')
    load()
  } catch { /* cancel */ }
}

onMounted(load)
</script>
