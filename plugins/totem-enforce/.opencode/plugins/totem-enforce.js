import { execSync } from "child_process"

let searchedThisTurn = {}

const FTS5_SPECIAL = /[:"+*^()~]/g
function sanitizeFts5(q) { return q.replace(FTS5_SPECIAL, " ").trim() }

function tokenize(sk) {
  const m = sk.match(/^(read|grep|bash|glob):(.*)/)
  if (!m) return []
  const [, t, raw] = m
  if (t === "bash") {
    const cmd = raw.replace(/^cd\s+\S+\s*&&\s*/, "").replace(/^cd\s+\S+\s*;\s*/, "").trim()
    const n = cmd.split(/\s+/)[0]?.split("/").pop()?.toLowerCase()
    return n && n.length > 2 ? [n] : []
  }
  return [...new Set(sanitizeFts5(raw).split(/[/\\._\- ='"{},]+/).map(w => w.toLowerCase()).filter(w => w.length > 2))]
}

function checkMem(dir, sk) {
  const words = tokenize(sk)
  if (!words.length) return false
  const isBash = sk.startsWith("bash:")
  for (const w of words) {
    try {
      let cmd = `totem search --query "${w}" --limit 1`
      if (isBash) cmd += ` --tags "cmd:${w}"`
      const r = execSync(cmd, { timeout: 5000, encoding: "utf-8", cwd: dir, stdio: ["pipe", "pipe", "pipe"] })
      if (JSON.parse(r || "[]").length > 0) return true
    } catch {}
  }
  return false
}

function storeImpl(dir, fp) {
  const fn = fp.split("/").pop()
  try {
    execSync(`totem create --type implementation --title "File: ${fn}" --statement "Agent read ${fp}" --tags "implementation,read,${fn},${fp}" --project "${dir}" --metadata '${JSON.stringify({ subject: fp, kind: "module", path: fp })}'`, { timeout: 10000, stdio: "pipe" })
  } catch {}
}

export default async ({ directory } = {}) => {
  const dir = directory || process.cwd()
  return {
    config: async (cfg) => {},
    "tool.execute.before": async (inp, out) => {
      const t = inp.tool
      if (!["grep", "glob", "read", "bash"].includes(t)) return
      let sk = ""
      if (t === "grep") sk = `grep:${out.args?.pattern || out.args?.regex || ""}`
      else if (t === "glob") sk = `glob:${out.args?.pattern || ""}`
      else if (t === "read") sk = `read:${out.args?.filePath || ""}`
      else if (t === "bash") sk = `bash:${out.args?.command || ""}`
      if (!sk || searchedThisTurn[sk]) return
      if (!checkMem(dir, sk)) return
      searchedThisTurn[sk] = new Date().toISOString()
      let redir = "memory_search_tool"
      if (t === "read") redir = "engineering_context_tool"
      if (t === "bash") redir = "memory_commands_tool"
      throw new Error(`Totem has memory about this. Use ${redir} first. Only ${t} the codebase if memory returns nothing relevant.`)
    },
    "tool.execute.after": async (inp, out) => {
      if (inp.tool === "read" && out.args?.filePath) storeImpl(dir, out.args.filePath)
    },
  }
}
