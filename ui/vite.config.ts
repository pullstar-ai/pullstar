import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const __dirname = fileURLToPath(new URL('.', import.meta.url))
// .pullstar/ lives one level above ui/
const pullstarDir = path.resolve(__dirname, '../.pullstar')

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'pullstar-static',
      configureServer(server) {
        // Serve .pullstar/*.json at /api/pullstar/{filename}
        // Usage: fetch('/api/pullstar/output_jsmith.json')
        server.middlewares.use('/api/pullstar', (req, res, next) => {
          // Strip query string, then take only the basename (no path traversal)
          const filename = path.basename((req.url ?? '').split('?')[0])
          if (!filename || !filename.endsWith('.json')) {
            next()
            return
          }

          const filepath = path.join(pullstarDir, filename)
          if (!fs.existsSync(filepath)) {
            res.statusCode = 404
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ error: `${filename} not found in .pullstar/` }))
            return
          }

          res.setHeader('Content-Type', 'application/json')
          res.end(fs.readFileSync(filepath))
        })
      },
    },
  ],
})
