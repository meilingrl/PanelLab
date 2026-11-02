import {mount,flushPromises} from '@vue/test-utils'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {createMemoryHistory} from 'vue-router'
import DashboardLayout from '../src/views/DashboardLayout.vue'
vi.mock('../src/theme',()=>({getTheme:()=> 'light',toggleTheme:()=> 'dark'}))
let wrapper
beforeEach(()=>{vi.stubGlobal('localStorage',{getItem:()=> 'token'});vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({items:[]})})))})
afterEach(()=>{wrapper?.unmount();vi.unstubAllGlobals()})
it('home and signed-in login land in workspaces; sidebar first entry and brand return there',async()=>{
 const {default:sourceRouter}=await import('../src/router')
 const {createRouter}=await import('vue-router')
 const router=createRouter({history:createMemoryHistory(),routes:sourceRouter.options.routes})
 await router.push('/');await router.isReady()
 expect(router.currentRoute.value.path).toBe('/workspaces')
 // Exercise the actual global guard too (memory router intentionally has no copied guard).
 await sourceRouter.push('/login')
 expect(sourceRouter.currentRoute.value.path).toBe('/workspaces')
 wrapper=mount(DashboardLayout,{global:{plugins:[router],stubs:{RouterView:true}}});await flushPromises()
 expect(wrapper.findAll('nav a')[0].attributes('href')).toBe('/workspaces')
 await router.push('/settings');await wrapper.get('.sidebar-header').trigger('click');await flushPromises()
 expect(router.currentRoute.value.path).toBe('/workspaces')
})
