/**
 * Totem Enforcement Plugin for OpenCode
 *
 * Three enforcement gates:
 * 1. Pre-read: block read/grep/bash if memory already has relevant items
 * 2. Post-read commit gate: block all tools until agent stores what it learned
 * 3. Post-write commit gate: block all tools until agent stores what it changed
 */

import { execSync } from "child_process"
import { appendFileSync } from "fs"

const DEBUG_LOG = "/tmp/totem-enforce-debug.log"

function debug(msg) {
  try { appendFileSync(DEBUG_LOG, `[${new Date().toISOString()}] ${msg}\n`) } catch {}
}

let searchedThisTurn = {}
let pendingStores = []
let pendingWrites = []

function checkMemory(projectDir, searchKey) {
  try {
    const result = execSync(
      `totem search --query "${searchKey}" --limit 1 --project "${projectDir}"`,
      { timeout: 5000, encoding: "utf-8", stdio: ["pipe", "pipe", "pipe"] }
    )
    const items = JSON.parse(result || "[]")
    return items.length > 0
  } catch {
    return false
  }
}

function isTool(inputTool, ...names) {
  return names.some(n => inputTool === n || inputTool === `totem_${n}`)
}

function normalizePath(p, projectDir) {
  if (!p) return p
  if (p.startsWith(projectDir)) return p.slice(projectDir.length + 1)
  return p
}

export default async ({ project, client, $, directory }) => {
  return {
    "tool.execute.before": async (input, output) => {
      const tool = input.tool
      const args = output.args

      debug(`before: ${tool}`)

      if (pendingWrites.length > 0) {
        const ok = isTool(tool, "register_file_write_tool") ||
          (isTool(tool, "memory_create_tool") && args?.type === "implementation" && args?.metadata?.changeType === "write")
        if (!ok) {
          const file = pendingWrites[0]
          throw new Error(
            `You wrote to ${file} but haven't registered the change. ` +
            `Call register_file_write_tool(path='${file}', statement='what changed', reason='why', ...) first.`
          )
        }
      }

      if (pendingStores.length > 0) {
        const ok = isTool(tool, "register_file_read_tool") ||
          (isTool(tool, "memory_create_tool") && args?.type === "implementation")
        if (!ok) {
          const file = pendingStores[0]
          throw new Error(
            `You read ${file} but haven't stored what you learned. ` +
            `Call register_file_read_tool(path='${file}', statement='what you learned', ...) first.`
          )
        }
      }

      if (!["grep", "glob", "read", "bash"].includes(tool)) return

      let searchKey = ""
      if (tool === "grep") searchKey = `grep:${args.pattern || args.regex || ""}`
      else if (tool === "glob") searchKey = `glob:${args.pattern || ""}`
      else if (tool === "read") searchKey = `read:${args.filePath || ""}`
      else if (tool === "bash") searchKey = `bash:${args.command || ""}`

      if (!searchKey) return
      if (searchedThisTurn[searchKey]) return

      const hasMemory = checkMemory(directory, searchKey)
      if (!hasMemory) return

      searchedThisTurn[searchKey] = new Date().toISOString()

      let redirect = "memory_search_tool"
      if (tool === "read") redirect = "engineering_context_tool"
      if (tool === "bash") redirect = "memory_commands_tool"

      throw new Error(
        `Totem has memory about this. Use ${redirect} first. ` +
        `Only ${tool} the codebase if memory returns nothing relevant.`
      )
    },

    "tool.execute.after": async (input, output) => {
      debug(`after: ${input.tool}`)

      if (input.tool === "read" && input.args?.filePath) {
        const p = normalizePath(input.args.filePath, directory)
        if (!pendingStores.includes(p)) pendingStores.push(p)
        debug(`after-read: pendingStores=${JSON.stringify(pendingStores)}`)
      }

      if ((input.tool === "edit" || input.tool === "write") && input.args?.filePath) {
        const p = normalizePath(input.args.filePath, directory)
        if (!pendingWrites.includes(p)) pendingWrites.push(p)
        debug(`after-write: pendingWrites=${JSON.stringify(pendingWrites)}`)
      }

      if (isTool(input.tool, "register_file_read_tool") && input.args?.path) {
        const p = normalizePath(input.args.path, directory)
        const idx = pendingStores.indexOf(p)
        if (idx !== -1) {
          pendingStores.splice(idx, 1)
          debug(`cleared-read: ${p} ok`)
        } else {
          debug(`cleared-read: ${p} not found in ${JSON.stringify(pendingStores)}`)
        }
      }

      if (isTool(input.tool, "register_file_write_tool") && input.args?.path) {
        const p = normalizePath(input.args.path, directory)
        const idx = pendingWrites.indexOf(p)
        if (idx !== -1) {
          pendingWrites.splice(idx, 1)
          debug(`cleared-write: ${p} ok`)
        } else {
          debug(`cleared-write: ${p} not found in ${JSON.stringify(pendingWrites)}`)
        }
      }

      if (isTool(input.tool, "memory_create_tool") && input.args?.type === "implementation") {
        const meta = input.args.metadata || {}
        if (meta.path) {
          const p = normalizePath(meta.path, directory)
          const idxStore = pendingStores.indexOf(p)
          if (idxStore !== -1) pendingStores.splice(idxStore, 1)
          const idxWrite = pendingWrites.indexOf(p)
          if (idxWrite !== -1) pendingWrites.splice(idxWrite, 1)
        }
      }
    },

    "session.idle": async () => {
      searchedThisTurn = {}
      pendingStores = []
      pendingWrites = []
    },
  }
}
