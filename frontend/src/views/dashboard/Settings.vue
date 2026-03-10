<script setup>
import { onMounted, ref } from 'vue'

const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const pwdError = ref('')
const pwdSuccess = ref('')
const pwdLoading = ref(false)

const sectionsOpen = ref({
  help: true,
  servers: true,
  security: true,
})

const servers = ref([])
const serversLoading = ref(false)
const serversError = ref('')
const serverSuccess = ref('')
const serverSaving = ref(false)
const editingId = ref(null)

const serverForm = ref({
  name: '',
  host: '',
  port: 22,
  username: '',
  password: '',
})

function toggleSection(key) {
  sectionsOpen.value[key] = !sectionsOpen.value[key]
}

async function submitPassword() {
  pwdError.value = ''
  pwdSuccess.value = ''
  if (!oldPassword.value) {
    pwdError.value = '请输入原密码'
    return
  }
  if (!newPassword.value) {
    pwdError.value = '请输入新密码'
    return
  }
  if (newPassword.value.length < 6) {
    pwdError.value = '新密码至少 6 位'
    return
  }
  if (oldPassword.value === newPassword.value) {
    pwdError.value = '新密码不能与原密码相同'
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    pwdError.value = '两次输入的新密码不一致'
    return
  }
  pwdLoading.value = true
  try {
    const res = await fetch('/api/auth/change-password', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${localStorage.getItem('panel_token')}`,
      },
      body: JSON.stringify({
        old_password: oldPassword.value,
        new_password: newPassword.value,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      pwdError.value = data.detail || data.message || '修改失败'
      return
    }
    pwdSuccess.value = '密码已更新，请使用新密码登录'
    oldPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
  } catch (e) {
    pwdError.value = '网络错误，请稍后重试'
  } finally {
    pwdLoading.value = false
  }
}

async function loadServers() {
  serversLoading.value = true
  serversError.value = ''
  try {
    const res = await fetch('/api/servers', {
      headers: {
        Authorization: `Bearer ${localStorage.getItem('panel_token')}`,
      },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      serversError.value = data.detail || data.message || '加载服务器列表失败'
      return
    }
    servers.value = Array.isArray(data.items) ? data.items : []
  } catch (e) {
    serversError.value = '网络错误，请稍后重试'
  } finally {
    serversLoading.value = false
  }
}

function resetServerForm() {
  editingId.value = null
  serverForm.value = {
    name: '',
    host: '',
    port: 22,
    username: '',
    password: '',
  }
  serverSuccess.value = ''
  serversError.value = ''
}

function startCreateServer() {
  resetServerForm()
}

function startEditServer(s) {
  editingId.value = s.id
  serverForm.value = {
    name: s.name,
    host: s.host,
    port: s.port,
    username: s.username,
    password: '',
  }
  serverSuccess.value = ''
  serversError.value = ''
}

async function submitServer() {
  serversError.value = ''
  serverSuccess.value = ''
  const body = {
    name: serverForm.value.name.trim(),
    host: serverForm.value.host.trim(),
    port: Number(serverForm.value.port) || 22,
    username: serverForm.value.username.trim(),
  }
  if (serverForm.value.password) {
    body.password = serverForm.value.password
  }
  if (!body.name || !body.host || !body.username) {
    serversError.value = '名称、IP 和用户名必填'
    return
  }
  serverSaving.value = true
  try {
    const url =
      editingId.value == null ? '/api/servers' : `/api/servers/${editingId.value}`
    const method = editingId.value == null ? 'POST' : 'PUT'
    const res = await fetch(url, {
      method,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${localStorage.getItem('panel_token')}`,
      },
      body: JSON.stringify(body),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      serversError.value = data.detail || data.message || '保存失败'
      return
    }
    serverSuccess.value = editingId.value == null ? '已添加服务器' : '已更新服务器'
    await loadServers()
    serverForm.value.password = ''
  } catch (e) {
    serversError.value = '网络错误，请稍后重试'
  } finally {
    serverSaving.value = false
  }
}

async function deleteServer(id) {
  if (!window.confirm('确认删除该服务器？')) return
  serversError.value = ''
  serverSuccess.value = ''
  try {
    const res = await fetch(`/api/servers/${id}`, {
      method: 'DELETE',
      headers: {
        Authorization: `Bearer ${localStorage.getItem('panel_token')}`,
      },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      serversError.value = data.detail || data.message || '删除失败'
      return
    }
    if (editingId.value === id) {
      resetServerForm()
    }
    await loadServers()
    serverSuccess.value = '服务器已删除'
  } catch (e) {
    serversError.value = '网络错误，请稍后重试'
  }
}

onMounted(() => {
  loadServers()
})
</script>

<template>
  <div class="page-settings">
    <h1 class="page-title">用户中心</h1>
    <p class="page-desc">管理账号、服务器数据中心与快捷帮助。</p>

    <section class="settings-section">
      <button type="button" class="section-header" @click="toggleSection('servers')">
        <h2 class="section-title">服务器数据中心</h2>
        <span class="section-toggle">{{ sectionsOpen.servers ? '收起' : '展开' }}</span>
      </button>
      <div v-if="sectionsOpen.servers" class="section-body">
        <p class="section-desc">
          在这里集中管理你名下的所有 SSH 服务器，终端与监控等功能会复用这份列表。
        </p>
        <div class="servers-layout">
          <div class="servers-list">
            <div class="servers-list-header">
              <h3 class="sub-title">服务器列表</h3>
              <button type="button" class="btn-secondary" @click="startCreateServer">
                新建服务器
              </button>
            </div>
            <p v-if="serversLoading" class="msg">加载中…</p>
            <p v-else-if="servers.length === 0" class="msg">还没有添加任何服务器。</p>
            <table v-else class="servers-table">
              <thead>
                <tr>
                  <th>名称</th>
                  <th>IP / 主机名</th>
                  <th>端口</th>
                  <th>用户名</th>
                  <th class="col-actions">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in servers" :key="s.id">
                  <td>{{ s.name }}</td>
                  <td>{{ s.host }}</td>
                  <td>{{ s.port }}</td>
                  <td>{{ s.username }}</td>
                  <td class="col-actions">
                    <button type="button" class="btn-link" @click="startEditServer(s)">
                      编辑
                    </button>
                    <button type="button" class="btn-link danger" @click="deleteServer(s.id)">
                      删除
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="servers-form">
            <h3 class="sub-title">
              {{ editingId == null ? '新建服务器' : '编辑服务器' }}
            </h3>
            <form class="settings-form" @submit.prevent="submitServer">
              <div class="field">
                <label for="srv-name">名称</label>
                <input
                  id="srv-name"
                  v-model="serverForm.name"
                  type="text"
                  placeholder="例如：生产服务器"
                />
              </div>
              <div class="field">
                <label for="srv-host">IP / 主机名</label>
                <input
                  id="srv-host"
                  v-model="serverForm.host"
                  type="text"
                  placeholder="例如：192.168.0.10"
                />
              </div>
              <div class="field-inline">
                <div class="field">
                  <label for="srv-port">端口</label>
                  <input
                    id="srv-port"
                    v-model="serverForm.port"
                    type="number"
                    min="1"
                    max="65535"
                  />
                </div>
                <div class="field">
                  <label for="srv-user">用户名</label>
                  <input
                    id="srv-user"
                    v-model="serverForm.username"
                    type="text"
                    placeholder="例如：root"
                  />
                </div>
              </div>
              <div class="field">
                <label for="srv-pass">密码</label>
                <input
                  id="srv-pass"
                  v-model="serverForm.password"
                  type="password"
                  autocomplete="new-password"
                  placeholder="留空则不修改已有密码"
                />
              </div>
              <p v-if="serversError" class="msg error-msg">{{ serversError }}</p>
              <p v-if="serverSuccess" class="msg success-msg">{{ serverSuccess }}</p>
              <button type="submit" class="btn-submit" :disabled="serverSaving">
                {{ serverSaving ? '保存中…' : '保存服务器' }}
              </button>
            </form>
          </div>
        </div>
      </div>
    </section>

    <section class="settings-section">
      <button type="button" class="section-header" @click="toggleSection('security')">
        <h2 class="section-title">账号与安全</h2>
        <span class="section-toggle">{{ sectionsOpen.security ? '收起' : '展开' }}</span>
      </button>
      <div v-if="sectionsOpen.security" class="section-body">
        <form class="settings-form" @submit.prevent="submitPassword">
          <div class="field">
            <label for="cp-old">原密码</label>
            <input
              id="cp-old"
              v-model="oldPassword"
              type="password"
              autocomplete="current-password"
              placeholder="请输入原密码"
            />
          </div>
          <div class="field">
            <label for="cp-new">新密码</label>
            <input
              id="cp-new"
              v-model="newPassword"
              type="password"
              autocomplete="new-password"
              placeholder="至少 6 位"
            />
          </div>
          <div class="field">
            <label for="cp-confirm">确认新密码</label>
            <input
              id="cp-confirm"
              v-model="confirmPassword"
              type="password"
              autocomplete="new-password"
              placeholder="再次输入新密码"
            />
          </div>
          <p v-if="pwdError" class="msg error-msg">{{ pwdError }}</p>
          <p v-if="pwdSuccess" class="msg success-msg">{{ pwdSuccess }}</p>
          <button type="submit" class="btn-submit" :disabled="pwdLoading">
            {{ pwdLoading ? '提交中…' : '确认修改' }}
          </button>
        </form>
      </div>
    </section>

    <section class="settings-section">
      <button type="button" class="section-header" @click="toggleSection('help')">
        <h2 class="section-title">快捷帮助</h2>
        <span class="section-toggle">{{ sectionsOpen.help ? '收起' : '展开' }}</span>
      </button>
      <div v-if="sectionsOpen.help" class="section-body">
        <div class="help-links">
          <router-link to="/manual" class="btn-secondary">使用手册</router-link>
          <router-link to="/feedback" class="btn-secondary">反馈信箱</router-link>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page-settings {
  max-width: 960px;
}

.page-title {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 0.5rem 0;
}

.page-desc {
  margin: 0 0 1.5rem 0;
  font-size: 0.95rem;
  color: var(--text-secondary);
}

.settings-section {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 12px;
  margin-bottom: 1.5rem;
  overflow: hidden;
}

.section-header {
  width: 100%;
  padding: 1rem 1.5rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--bg-secondary);
  border: none;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
}

.section-title {
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.section-toggle {
  font-size: 0.85rem;
  color: var(--text-secondary);
}

.section-body {
  padding: 1.25rem 1.5rem 1.5rem 1.5rem;
}

.section-desc {
  margin: 0 0 1rem 0;
  font-size: 0.9rem;
  color: var(--text-secondary);
}

.servers-layout {
  display: flex;
  flex-direction: row;
  gap: 1.5rem;
}

.servers-list {
  flex: 3;
  min-width: 0;
}

.servers-form {
  flex: 2;
  min-width: 0;
}

.servers-list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.75rem;
}

.sub-title {
  margin: 0 0 0.5rem 0;
  font-size: 1rem;
  font-weight: 600;
  color: var(--text-primary);
}

.servers-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}

.servers-table th,
.servers-table td {
  padding: 0.5rem 0.5rem;
  border-bottom: 1px solid var(--border);
  text-align: left;
  white-space: nowrap;
}

.servers-table th:nth-child(2),
.servers-table td:nth-child(2) {
  max-width: 180px;
}

.col-actions {
  text-align: right;
}

.field {
  margin-bottom: 1rem;
}

.field-inline {
  display: flex;
  gap: 1rem;
}

.field label {
  display: block;
  font-size: 0.9rem;
  color: var(--text-secondary);
  margin-bottom: 0.35rem;
}

.settings-form .field input,
.settings-form .field-inline input {
  width: 100%;
  padding: 0.5rem 0.75rem;
  font-size: 0.95rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-primary);
  box-sizing: border-box;
}

.settings-form input:focus {
  outline: none;
  border-color: var(--text-muted);
}

.help-links {
  display: flex;
  gap: 1rem;
}

.btn-secondary {
  display: inline-block;
  padding: 0.5rem 1rem;
  font-size: 0.9rem;
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
  text-decoration: none;
  transition: all 0.2s;
}

.btn-secondary:hover {
  color: var(--text-primary);
  background: var(--bg-tertiary);
}

.msg {
  font-size: 0.9rem;
  margin: 0 0 0.75rem 0;
}

.error-msg {
  color: var(--error, #c00);
}

.success-msg {
  color: var(--text-secondary);
}

.btn-submit {
  padding: 0.5rem 1rem;
  font-size: 0.9rem;
  color: #fff;
  background: var(--accent, #2563eb);
  border: none;
  border-radius: 6px;
  cursor: pointer;
  transition: opacity 0.2s;
}

.btn-submit:hover:not(:disabled) {
  opacity: 0.9;
}

.btn-submit:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-link {
  padding: 0;
  border: none;
  background: none;
  color: var(--accent, #2563eb);
  font-size: 0.85rem;
  cursor: pointer;
  margin-left: 0.5rem;
}

.btn-link:first-of-type {
  margin-left: 0;
}

.btn-link.danger {
  color: var(--error, #c00);
}

@media (max-width: 900px) {
  .servers-layout {
    flex-direction: column;
  }
}
</style>
