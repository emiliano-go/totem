import { execFileSync } from "child_process"

// Totem enforcement plugin for OpenCode.
// 1. Read gate: block read/grep/glob/bash when totem has relevant memory,
//    redirecting to the memory tools. Retry after checking memory is allowed.
// 2. Read commit-gate: after a successful read, all non-totem tools are
//    blocked until totem_register_file_read_tool is called.
// 3. Write commit-gate: after edit/write, blocked until
//    totem_register_file_write_tool is called.
// State is per-session (keyed by sessionID), reset on session.idle.

const sessions = new Map()

const FTS5_SPECIAL = /[:"'+*^()~]/g
const STOP_WORDS = new Set(["the", "and", "for", "not", "with", "from", "this", "that"])
const SUBCMDS = /^(grep|find|cat|head|tail|wc|sort|uniq|awk|sed|less|more|diff|comm|xargs|file|rg|ag|ack|jq)$/

function getState(sessionID) {
  let s = sessions.get(sessionID)
  if (!s) {
    s = { searched: {}, pendingRead: null, pendingWrite: null }
    sessions.set(sessionID, s)
  }
  return s
}

function sanitizeFts5(q) { return q.replace(FTS5_SPECIAL, " ").trim() }

function tokenize(raw) {
  return [...new Set(
    sanitizeFts5(raw).split(/[/\\._\- ='"{},]+/)
      .map(w => w.toLowerCase())
      .filter(w => w.length > 2 && !STOP_WORDS.has(w))
  )]
}

function tokenizeBash(command) {
  let cmd = command.trim()
    .replace(/^cd\s+\S+\s*&&\s*/, "")
    .replace(/^cd\s+\S+\s*;\s*/, "")
  const first = cmd.split(/\s+/)[0]?.split("/").pop()?.toLowerCase() || ""
  if (SUBCMDS.test(first)) return tokenize(cmd)
  return first.length > 2 ? [first] : []
}

function totemSearch(dir, query, { types, tags } = {}) {
  try {
    // Arg array, no shell — agent-controlled queries can't inject.
    // --project is a group-level option: it must precede the subcommand.
    const args = ["--project", dir, "search", "--query", query, "--limit", "1"]
    if (types) args.push("--types", types)
    if (tags) args.push("--tags", tags)
    const r = execFileSync("totem", args, { timeout: 5000, encoding: "utf-8", cwd: dir, stdio: ["pipe", "pipe", "pipe"] })
    return JSON.parse(r || "[]").length > 0
  } catch {
    return false // Fail open
  }
}

function hasMemoryFor(dir, tool, args) {
  if (tool === "read") {
    const fp = args?.filePath || ""
    if (!fp) return false
    const name = fp.split("/").pop()
    return totemSearch(dir, fp, { types: "implementation" })
      || (name && totemSearch(dir, name, { types: "implementation" }))
  }
  if (tool === "grep" || tool === "glob") {
    for (const w of tokenize(args?.pattern || args?.regex || "")) {
      if (totemSearch(dir, w)) return true
    }
    return false
  }
  if (tool === "bash") {
    const command = args?.command || ""
    const first = command.trim().split(/\s+/)[0]?.split("/").pop()?.toLowerCase() || ""
    if (!SUBCMDS.test(first)) {
      return first && totemSearch(dir, first, { tags: `cmd:${first}` })
    }
    for (const w of tokenizeBash(command)) {
      if (totemSearch(dir, w)) return true
    }
    return false
  }
  return false
}

function buildSearchKey(tool, args) {
  if (tool === "grep") return `grep:${args?.pattern || args?.regex || ""}`
  if (tool === "glob") return `glob:${args?.pattern || ""}`
  if (tool === "read") return `read:${args?.filePath || ""}`
  if (tool === "bash") return `bash:${args?.command || ""}`
  return ""
}

export const TotemEnforce = async ({ directory } = {}) => {
  const dir = directory || process.cwd()
  return {
    "tool.execute.before": async (input, output) => {
      const tool = input.tool
      const state = getState(input.sessionID || "default")

      // 1. Register calls clear their gate.
      if (tool === "totem_register_file_read_tool") { state.pendingRead = null; return }
      if (tool === "totem_register_file_write_tool") { state.pendingWrite = null; return }

      // 2. Commit-gate: pending registration blocks all non-totem tools.
      if (!tool.startsWith("totem_")) {
        if (state.pendingRead) {
          throw new Error(
            `You read ${state.pendingRead}. You MUST call register_file_read_tool ` +
            `with what you learned (path, subject, kind, statement, tags) before doing anything else.`
          )
        }
        if (state.pendingWrite) {
          throw new Error(
            `You modified ${state.pendingWrite}. You MUST call register_file_write_tool ` +
            `documenting what changed and why before doing anything else.`
          )
        }
      }

      // Remember read/edit/write target paths so the after-hook can arm the
      // gate on success (the after-hook does not receive args).
      if (["read", "edit", "write"].includes(tool) && output.args?.filePath) {
        state.lastCall = state.lastCall || {}
        state.lastCall[input.callID] = { tool, filePath: output.args.filePath }
      }

      // 3. Memory gates for search/read tools.
      if (!["read", "grep", "glob", "bash"].includes(tool)) return
      const sk = buildSearchKey(tool, output.args || {})
      if (!sk || state.searched[sk]) return
      if (!hasMemoryFor(dir, tool, output.args || {})) return
      state.searched[sk] = true
      const redirect = tool === "read" ? "engineering_context_tool (with paths=[...]) or memory_search_tool"
        : tool === "bash" ? "memory_commands_tool"
        : "memory_search_tool"
      throw new Error(
        `Totem has memory about this. Use ${redirect} first. ` +
        `Only ${tool} the codebase if memory returns nothing relevant. Do not bypass via another tool.`
      )
    },

    "tool.execute.after": async (input, output) => {
      const state = getState(input.sessionID || "default")
      const call = state.lastCall?.[input.callID]
      if (!call) return
      delete state.lastCall[input.callID]
      // Don't arm gates for failed tool calls.
      if (output.error) return
      if (call.tool === "read") state.pendingRead = call.filePath
      if (call.tool === "edit" || call.tool === "write") state.pendingWrite = call.filePath
    },

    event: async ({ event }) => {
      // Clear only the session that went idle, not all sessions.
      if (event.type === "session.idle") {
        const sid = event.properties?.sessionID
        if (sid) sessions.delete(sid)
        else sessions.clear()
      }
    },
  }
}

export default TotemEnforce
