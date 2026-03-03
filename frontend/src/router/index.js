import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/Login.vue'),
    meta: { public: true },
  },
  {
    path: '/register',
    name: 'Register',
    component: () => import('../views/Register.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    component: () => import('../views/DashboardLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      {
        path: '',
        name: 'Overview',
        component: () => import('../views/dashboard/Overview.vue'),
      },
      {
        path: 'monitor',
        name: 'Monitor',
        component: () => import('../views/dashboard/Monitor.vue'),
      },
      {
        path: 'sites',
        name: 'Sites',
        component: () => import('../views/dashboard/Sites.vue'),
      },
      {
        path: 'databases',
        name: 'Databases',
        component: () => import('../views/dashboard/Databases.vue'),
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

function getToken() {
  return localStorage.getItem('panel_token')
}

router.beforeEach((to, _from, next) => {
  const token = getToken()
  if (to.meta.public) {
    if (token && (to.name === 'Login' || to.name === 'Register')) {
      next({ name: 'Overview' })
    } else {
      next()
    }
    return
  }
  if (to.meta.requiresAuth && !token) {
    next({ name: 'Login', query: { redirect: to.fullPath } })
    return
  }
  next()
})

export default router
