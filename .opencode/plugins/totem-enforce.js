import { execSync } from "child_process"

let searchedThisTurn = {}

const FTS5_SPECIAL = /[:"+*^()~]/g
function sanitizeFts5(q) { return q.replace(FTS5_SPECIAL, " ").trim() }
function stripSpecials(q) { return q.replace(/[:"'+*^()~]/g, "").trim() }

// Sub-commands that search/read file content (not just run a binary)
const SUBCMDS = /^(grep|find|cat|head|tail|wc|sort|uniq|awk|sed|less|more|diff|comm|xargs|file|rg|ag|ack|jq)$/

function tokenize(sk) {
  const m = sk.match(/^(read|grep|bash|glob):(.*)/)
  if (!m) return []
  const [, t, raw] = m
  if (t === "bash") {
    let cmd = raw.replace(/^cd\s+\S+\s*&&\s*/, "").replace(/^cd\s+\S+\s*;\s*/, "").trim()
    // Strip additional cd chains
    cmd = cmd.replace(/^cd\s+\S+\s*&&\s*/, "").replace(/^cd\s+\S+\s*;\s*/, "").trim()
    const parts = cmd.split(/\s+/)
    const first = parts[0]?.split("/").pop()?.toLowerCase()

    // If first word is a sub-command that searches content, tokenize the full command
    if (first && SUBCMDS.test(first)) {
      return [...new Set(
        stripSpecials(cmd).split(/[/\\._\- =,\t]+/)
          .map(w => w.toLowerCase())
          .filter(w => w.length > 2 && !STOP.has(w))
      )]
    }
    // Otherwise just use the command name
    return first && first.length > 2 ? [first] : []
  }
  return [...new Set(stripSpecials(raw).split(/[/\\._\- =,\t]+/).map(w => w.toLowerCase()).filter(w => w.length > 2 && !STOP.has(w)))]
}

const STOP = new Set(["the", "and", "for", "not", "with", "from", "this", "that"])

function checkMem(dir, sk) {
  const words = tokenize(sk)
  if (!words.length) return false
  const isBash = sk.startsWith("bash:")
  const cmd = sk.replace(/^bash:/, "").trim()
  const first = cmd.split(/\s+/)[0]?.split("/").pop()?.toLowerCase()
  const hasSubcmd = first && SUBCMDS.test(first)

  for (const w of words) {
    try {
      let q = `totem search --query "${w}" --limit 1`
      // Only use cmd: tag for actual command names, not sub-command arguments
      if (isBash && !hasSubcmd) q += ` --tags "cmd:${w}"`
      const r = execSync(q, { timeout: 5000, encoding: "utf-8", cwd: dir, stdio: ["pipe", "pipe", "pipe"] })
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
      throw new Error(`Totem has memory about this. Use ${redir} first. Only ${t} the codebase if memory returns nothing relevant. Do not bypass by using bash to run ${t} indirectly.`)
    },
    "tool.execute.after": async (inp, out) => {
      if (inp.tool === "read" && out.args?.filePath) storeImpl(dir, out.args.filePath)
    },
  }
}
