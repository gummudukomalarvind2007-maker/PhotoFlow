import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

const entrypoint = resolve('services/backend/run_backend.py')
const python = process.env.PHOTOFLOW_PYTHON ?? 'python'

if (!existsSync(entrypoint)) throw new Error(`Backend entrypoint not found: ${entrypoint}`)

const child = spawn(python, [entrypoint, '--health-check'], { stdio: 'inherit', windowsHide: true })
child.on('error', (error) => {
  console.error(`Unable to start Python with '${python}'. Install Python 3.12 or set PHOTOFLOW_PYTHON. ${error.message}`)
  process.exitCode = 1
})
child.on('exit', (code) => { process.exitCode = code ?? 1 })

