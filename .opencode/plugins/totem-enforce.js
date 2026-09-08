/**
 * Totem Enforcement Plugin for OpenCode
 *
 * Three enforcement gates:
 * 1. Pre-read: block read/grep/bash if memory already has relevant items
 * 2. Post-read commit gate: block all tools until agent stores what it learned
 * 3. Post-write commit gate: block all tools until agent stores what it changed
 *
 * Install: copy to .opencode/plugins/totem-enforce.js
 */

import { execSync } from "child_process"
import { appendFileSync } from "fs"

const DEBUG_LOG = "/tmp/totem-enforce-debug.log"

function debug(msg) {
  try { appendFileSync(DEBUG_LOG, `[${new Date().toISOString()}] ${msg}\n`) } catch {}
}

// State: track what agent has searched this turn
let searchedThisTurn = {}

// State: files read but not yet stored (commit gate)
let pendingStores = []

// State: files written but not yet registered (commit gate)
let pendingWrites = []

/**
 * Check if totem has memory related to this query.
 */
function checkMemory(projectDir, searchKey) {
  try {
    const result = execSync(
      `totem search --query "${searchKey}" --limit 1 --project "${projectDir}"`,
      { timeout: 5000, encoding: "utf-8", stdio: ["pipe", "pipe", "pipe"] }
    )
    const items = JSON.parse(result || "[]")
    return items.length > 0
  } catch {
    return false // Fail open
  }
}

export default async ({ project, client, $, directory }) => {
  return {
    "tool.execute.before": async (input, output) => {
      const tool = input.tool
      const args = output.args

      debug(`before: ${tool} args=${JSON.stringify(args || {}).slice(0, 200)}`)

      // --- Commit gate: block until pending writes are resolved ---
      if (pendingWrites.length > 0) {
        const isWriteStoreCall =
          tool === "register_file_write" ||
          (tool === "memory_create" && args?.type === "implementation" && args?.metadata?.changeType === "write")
        if (!isWriteStoreCall) {
          const file = pendingWrites[0]
          throw new Error(
            `You wrote to ${file} but haven't registered the change. ` +
            `Call register_file_write(path='${file}', statement='what changed', reason='why', ...) first. ` +
            `This documents changes for future sessions.`
          )
        }
      }

      // --- Commit gate: block until pending reads are stored ---
      if (pendingStores.length > 0) {
        const isReadStoreCall =
          tool === "register_file_read" ||
          (tool === "memory_create" && args?.type === "implementation")
        if (!isReadStoreCall) {
          const file = pendingStores[0]
          throw new Error(
            `You read ${file} but haven't stored what you learned. ` +
            `Call register_file_read(path='${file}', statement='what you learned', ...) first. ` +
            `This prevents re-reading the same file in future sessions.`
          )
        }
      }

      // --- Pre-read gate: block if memory already exists ---
      // Only intercept grep, glob, read, bash
      if (!["grep", "glob", "read", "bash"].includes(tool)) return

      // Build search key
      let searchKey = ""
      if (tool === "grep") searchKey = `grep:${args.pattern || args.regex || ""}`
      else if (tool === "glob") searchKey = `glob:${args.pattern || ""}`
      else if (tool === "read") searchKey = `read:${args.filePath || ""}`
      else if (tool === "bash") searchKey = `bash:${args.command || ""}`

      if (!searchKey) return

      // Check if already searched this turn
      if (searchedThisTurn[searchKey]) return

      // Check if memory has relevant items
      const hasMemory = checkMemory(directory, searchKey)
      if (!hasMemory) return

      // Memory exists → block and redirect
      searchedThisTurn[searchKey] = new Date().toISOString()

      // Determine redirect tool
      let redirect = "memory_search_tool"
      if (tool === "read") redirect = "engineering_context_tool"
      if (tool === "bash") redirect = "memory_commands_tool"

      throw new Error(
        `Totem has memory about this. Use ${redirect} first. ` +
        `Only ${tool} the codebase if memory returns nothing relevant.`
      )
    },

    "tool.execute.after": async (input, output) => {
      debug(`after: ${input.tool} pendingStores=${pendingStores.length} pendingWrites=${pendingWrites.length}`)

      // After read: track file as pending store
      if (input.tool === "read" && output.args?.filePath) {
        const filePath = output.args.filePath
        if (!pendingStores.includes(filePath)) {
          pendingStores.push(filePath)
        }
      }

      // After edit/write: track file as pending write
      if ((input.tool === "edit" || input.tool === "write") && output.args?.filePath) {
        const filePath = output.args.filePath
        if (!pendingWrites.includes(filePath)) {
          pendingWrites.push(filePath)
        }
      }

      // After register_file_read: clear from pending stores
      if (input.tool === "register_file_read" && output.args?.path) {
        const idx = pendingStores.indexOf(output.args.path)
        if (idx !== -1) pendingStores.splice(idx, 1)
      }

      // After register_file_write: clear from pending writes
      if (input.tool === "register_file_write" && output.args?.path) {
        const idx = pendingWrites.indexOf(output.args.path)
        if (idx !== -1) pendingWrites.splice(idx, 1)
      }

      // After memory_create(type=implementation): clear matching file from both gates
      if (input.tool === "memory_create" && output.args?.type === "implementation") {
        const meta = output.args.metadata || {}
        if (meta.path) {
          const idxStore = pendingStores.indexOf(meta.path)
          if (idxStore !== -1) pendingStores.splice(idxStore, 1)
          const idxWrite = pendingWrites.indexOf(meta.path)
          if (idxWrite !== -1) pendingWrites.splice(idxWrite, 1)
        }
      }
    },

    // Turn boundary: clear state
    "session.idle": async () => {
      searchedThisTurn = {}
      pendingStores = []
      pendingWrites = []
    },
  }
}
