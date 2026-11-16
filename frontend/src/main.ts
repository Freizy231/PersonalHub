import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './tokens.css'
import './base.css'
import { initTheme } from './shared/theme'

// Before mount, so people on the dark theme never see a light flash.
initTheme()

const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/:pathMatch(.*)*', component: { template: '<span />' } }],
  scrollBehavior: () => ({ top: 0 }),
})
createApp(App).use(router).mount('#app')
