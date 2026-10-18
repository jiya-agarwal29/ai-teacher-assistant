import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')

  // Phase 7C item 9 / Phase 7D item 1: VITE_API_URL is compiled into the
  // bundle at build time (see services/api.js's API_BASE_URL), not read at
  // runtime, so a production build with it unset would silently ship
  // pointed at nothing real. Fail the build loudly instead -- see
  // README/DEPLOY.md for where this comes from on Render. Trimmed first --
  // pasting it into Render's dashboard can pick up a stray trailing
  // newline/space the same way a backend secret can (see backend/env_utils.py).
  const apiUrl = (env.VITE_API_URL || '').trim()

  if (mode === 'production' && !apiUrl) {
    throw new Error(
      'VITE_API_URL is required for a production build (it is compiled into the bundle, ' +
      'not read at runtime). Set it before building, e.g.:\n' +
      '  VITE_API_URL=https://your-backend.onrender.com npm run build\n' +
      'See README.md / DEPLOY.md.'
    )
  }

  if (mode === 'production' && apiUrl.endsWith('/')) {
    throw new Error(
      `VITE_API_URL must not have a trailing slash -- got "${apiUrl}". ` +
      'Every caller in services/api.js already starts its own path with "/", ' +
      'so a trailing slash here would produce a double "//" in every request URL. ' +
      'Remove the trailing "/" and rebuild.'
    )
  }

  return {
    plugins: [
      react(),
      tailwindcss(),
    ],
  }
})
