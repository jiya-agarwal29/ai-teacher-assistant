import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Separate from vite.config.js on purpose: that file enforces VITE_API_URL
// for a production app build, which has nothing to do with running tests.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.js'],
    globals: false,
  },
})
