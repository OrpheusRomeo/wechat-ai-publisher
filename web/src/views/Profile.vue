<template>
  <div>
    <h2>👤 个人设置</h2>
    <el-card style="max-width: 480px; margin-top: 20px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="用户名">
          <el-input v-model="userStore.username" disabled />
        </el-form-item>
        <el-form-item label="昵称">
          <el-input v-model="userStore.nickname" disabled />
        </el-form-item>
        <el-form-item label="角色">
          <el-tag :type="userStore.isSuperadmin ? 'danger' : 'primary'">{{ userStore.role }}</el-tag>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card style="max-width: 480px; margin-top: 20px" header="修改密码">
      <el-form ref="pwdFormRef" :model="pwdForm" :rules="pwdRules" label-width="100px">
        <el-form-item label="旧密码" prop="oldPassword">
          <el-input v-model="pwdForm.oldPassword" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码" prop="newPassword">
          <el-input v-model="pwdForm.newPassword" type="password" show-password />
        </el-form-item>
        <el-form-item label="确认密码" prop="confirmPassword">
          <el-input v-model="pwdForm.confirmPassword" type="password" show-password />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="changing" @click="handleChangePwd">修改密码</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { authApi } from '@/api/auth'
import { ElMessage } from 'element-plus'

const router = useRouter()
const userStore = useUserStore()

const pwdForm = reactive({ oldPassword: '', newPassword: '', confirmPassword: '' })
const pwdRules = {
  oldPassword: [{ required: true, message: '请输入旧密码', trigger: 'blur' }],
  newPassword: [{ required: true, min: 6, message: '密码至少 6 位', trigger: 'blur' }],
  confirmPassword: [
    { required: true, message: '请确认新密码', trigger: 'blur' },
    { validator: (_: any, v: string, cb: any) => v === pwdForm.newPassword ? cb() : cb(new Error('两次密码不一致')), trigger: 'blur' },
  ],
}
const changing = ref(false)

async function handleChangePwd() {
  changing.value = true
  try {
    await authApi.changePassword(pwdForm.oldPassword, pwdForm.newPassword)
    ElMessage.success('密码已修改，请重新登录')
    userStore.logout()
    setTimeout(() => router.push('/login'), 500)
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '修改失败')
  } finally { changing.value = false }
}
</script>
