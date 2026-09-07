/**
 * Totem Enforcement Plugin for OpenCode
 *
 * Forces agent to search totem memory before grep/read/bash.
 * Smart blocking: only blocks if memory is non-empty AND related to the query.
 *
 * Install: copy to .opencode/plugins/totem-enforce.js
 */

import { execSync } from "child_process"

// State: track what agent has searched this turn
let searchedThisTurn = {}

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

/**
 * Store implementation info after file read.
 */
function storeImplementationInfo(projectDir, filePath) {
  const fileName = filePath.split("/").pop()
  try {
    execSync(
      `totem create --type implementation ` +
      `--title "File: ${fileName}" ` +
      `--statement "Agent read ${filePath}" ` +
      `--tags "implementation,read,${fileName},${filePath}" ` +
      `--project "${projectDir}" ` +
      `--metadata '${JSON.stringify({ subject: filePath, kind: "module", path: filePath })}'`,
      { timeout: 10000, stdio: "pipe" }
    )
  } catch {
    // Silent fail — don't block the agent
  }
}

export const TotemEnforce = async ({ project, client, $, directory }) => {
  return {
    "tool.execute.before": async (input, output) => {
      const tool = input.tool
      const args = output.args

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
      // After read: store implementation info
      if (input.tool === "read" && output.args?.filePath) {
        storeImplementationInfo(directory, output.args.filePath)
      }
    },

    // Turn boundary: clear state
    "session.idle": async () => {
      searchedThisTurn = {}
    },
  }
}
