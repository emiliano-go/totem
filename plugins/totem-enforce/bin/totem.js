#!/usr/bin/env node
"use strict";

const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

const PKG_DIR = path.resolve(__dirname, "..");
const HOOKS_DIR = path.join(PKG_DIR, "hooks");

function hasCommand(cmd) {
  try {
    execSync(cmd, { stdio: "ignore", timeout: 5000 });
    return true;
  } catch {
    return false;
  }
}

function mkdirp(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function copyFile(src, dest) {
  fs.copyFileSync(src, dest);
  console.log(`  → ${path.relative(process.env.HOME || "~", dest)}`);
}

const PKG_VERSION = require(path.join(PKG_DIR, "package.json")).version;

function versionOlder(a, b) {
  const pa = String(a).split(".").map(Number);
  const pb = String(b).split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] || 0, y = pb[i] || 0;
    if (x !== y) return x < y;
  }
  return false;
}

function totemVersion() {
  try {
    const out = execSync("totem --version", { encoding: "utf-8", timeout: 5000 });
    const m = out.match(/(\d+\.\d+\.\d+)/);
    return m ? m[1] : null;
  } catch {
    return null;
  }
}

// MCP server command for agent configs. Pinned to this package's version and
// pre-warmed HERE (install/update time, i.e. when `npx totem` runs) so agent
// startups never hit the network; uvx caches environments per version, so
// launches are instant. Falls back to the PATH binary when uvx is missing
// or the pin cannot be resolved (e.g. PyPI lagging this npm release).
let mcpCommandCache = null;
function buildMcpCommand() {
  if (mcpCommandCache) return mcpCommandCache;
  if (!hasCommand("uvx --version")) return (mcpCommandCache = ["totem-mcp"]);
  const spec = `totem-mcp==${PKG_VERSION}`;
  try {
    execSync(`uvx --from "${spec}" totem --version`, { stdio: "ignore", timeout: 120000 });
    return (mcpCommandCache = ["uvx", spec]);
  } catch {
    return (mcpCommandCache = ["totem-mcp"]);
  }
}

// ── Kimi hooks wiring ─────────────────────────────────────────────

const KIMI_HOOKS_TOML = `[[hooks]]
event = "PreToolUse"
matcher = ".*"
command = "python3 ~/.kimi-code/hooks/totem-hook.py pre"
timeout = 10

[[hooks]]
event = "PostToolUse"
matcher = "Read|Edit|Write|MultiEdit|NotebookEdit"
command = "python3 ~/.kimi-code/hooks/totem-hook.py post"
timeout = 10

[[hooks]]
event = "UserPromptSubmit"
command = "python3 ~/.kimi-code/hooks/totem-hook.py clear"
timeout = 5
`;

// Appends the totem [[hooks]] entries to ~/.kimi-code/config.toml. TOML-safe:
// [[hooks]] blocks at EOF always extend the root hooks array. Idempotent: skips
// if totem-hook.py is already referenced; strips legacy per-script totem hooks.
function configureKimiHooks(configPath) {
  let raw = fs.existsSync(configPath) ? fs.readFileSync(configPath, "utf-8") : "";
  if (raw.includes("totem-hook.py")) {
    console.log("  → totem hooks already wired in config.toml");
    return;
  }
  if (/totem-(enforce|store-read|clear-state)\.py/.test(raw)) {
    raw = raw
      .split(/^(?=\[\[hooks\]\])/m)
      .filter((block) => !/totem-(enforce|store-read|clear-state)\.py/.test(block))
      .join("");
  }
  const cleaned = raw.trimEnd();
  const next = (cleaned ? cleaned + "\n\n" : "") +
    "# Totem enforcement hooks (managed by @emiliano-go/totem; do not edit)\n" +
    KIMI_HOOKS_TOML;
  fs.writeFileSync(configPath, next);
  console.log("  → Wired totem hooks in ~/.kimi-code/config.toml");
}

// ── 1. Install/upgrade totem-mcp ──────────────────────────────────

const current = totemVersion();
if (current && !versionOlder(current, PKG_VERSION)) {
  console.log(`[totem] totem-mcp ${current} satisfies ${PKG_VERSION}, skipping install`);
} else {
  if (current) {
    console.log(`[totem] totem-mcp ${current} is older than ${PKG_VERSION}. Upgrading...`);
  } else {
    console.log("[totem] totem-mcp not found. Installing...");
  }
  let installed = false;

  // Try pipx first (works on externally-managed Python like Arch Linux)
  if (!installed && hasCommand("pipx --version")) {
    try {
      execSync(current ? "pipx upgrade totem-mcp" : "pipx install totem-mcp", { stdio: "inherit", timeout: 120000 });
      installed = true;
    } catch {}
  }

  // Try uv tool (runs in isolated env, no system Python conflict)
  if (!installed && hasCommand("uv --version")) {
    try {
      execSync(current ? "uv tool upgrade totem-mcp" : "uv tool install totem-mcp", { stdio: "inherit", timeout: 120000 });
      installed = true;
    } catch {}
  }

  // Try pip (may fail on externally-managed environments)
  if (!installed && (hasCommand("pip --version") || hasCommand("pip3 --version"))) {
    const pip = hasCommand("pip --version") ? "pip" : "pip3";
    try {
      execSync(`${pip} install --user --upgrade totem-mcp`, { stdio: "inherit", timeout: 120000 });
      installed = true;
    } catch {}
  }

  // Check that it became available (e.g. already installed via pipx/uvx)
  if (!installed && !totemVersion()) {
    console.error("[totem] Install failed. Try one of:");
    console.error("  pipx install totem-mcp");
    console.error("  uv tool install totem-mcp");
    console.error("  pip install --user totem-mcp");
    process.exit(1);
  }
}

const version = totemVersion();
if (!version) {
  console.error("[totem] totem CLI not found on PATH after install.");
  process.exit(1);
}
console.log(`[totem] totem-mcp ${version}`);

// ── 2. Configure OpenCode ─────────────────────────────────────────

const opencodeDir = path.join(os.homedir(), ".config", "opencode", "plugins");
const opencodeBase = path.join(os.homedir(), ".config", "opencode");
const opencodeJsonPath = path.join(opencodeBase, "opencode.json");
const opencodeJsoncPath = path.join(opencodeBase, "opencode.jsonc");
const opencodeConfigPath = fs.existsSync(opencodeJsoncPath) ? opencodeJsoncPath : opencodeJsonPath;

if (hasCommand("opencode --version")) {
  console.log("[totem] Configuring OpenCode...");
  mkdirp(opencodeDir);
  copyFile(
    path.join(PKG_DIR, ".opencode", "plugins", "totem-enforce.js"),
    path.join(opencodeDir, "totem-enforce.js")
  );

  // Register plugin and MCP server in opencode config
  if (fs.existsSync(opencodeConfigPath)) {
    try {
      // Strip JSONC comments before parsing, but only outside string
      // literals, so URLs like "https://..." survive.
      let raw = fs.readFileSync(opencodeConfigPath, "utf-8");
      raw = raw.replace(/("(?:[^"\\]|\\.)*")|\/\/[^\n]*|\/\*[\s\S]*?\*\//g, (m, s) => s || "");
      const config = JSON.parse(raw);
      let changed = false;

      const pluginEntry = "@emiliano-go/totem";
      if (!config.plugin) config.plugin = [];
      if (!config.plugin.includes(pluginEntry)) {
        config.plugin.push(pluginEntry);
        changed = true;
        console.log(`  → Added ${pluginEntry} to ${path.basename(opencodeConfigPath)}`);
      } else {
        console.log(`  → ${pluginEntry} already in ${path.basename(opencodeConfigPath)}`);
      }

      if (!config.mcp) config.mcp = {};
      const mcpCommand = buildMcpCommand();
      const existing = config.mcp.totem;
      if (!existing || JSON.stringify(existing.command) !== JSON.stringify(mcpCommand)) {
        config.mcp.totem = {
          type: "local",
          command: mcpCommand,
          enabled: true,
        };
        changed = true;
        console.log(`  → Set totem MCP server in ${path.basename(opencodeConfigPath)}`);
      } else {
        console.log(`  → totem MCP server already correct`);
      }

      if (changed) {
        fs.writeFileSync(opencodeConfigPath, JSON.stringify(config, null, 2) + "\n");
      }
    } catch (e) {
      console.log(`  → Could not update ${path.basename(opencodeConfigPath)}: ${e.message}`);
    }
  } else {
    // Create config with plugin and MCP server
    const config = {
      plugin: ["@emiliano-go/totem"],
      mcp: {
        totem: {
          type: "local",
          command: buildMcpCommand(),
          enabled: true,
        },
      },
    };
    fs.writeFileSync(opencodeJsonPath, JSON.stringify(config, null, 2) + "\n");
    console.log(`  → Created opencode.json with plugin and MCP server`);
  }
}

// ── 3. Configure Claude Code ──────────────────────────────────────

const claudeHome = path.join(os.homedir(), ".claude");
const claudeHooksDir = path.join(claudeHome, "hooks");
const claudeSettingsPath = path.join(claudeHome, "settings.json");

if (fs.existsSync(claudeHome) || hasCommand("claude --version")) {
  console.log("[totem] Configuring Claude Code...");
  mkdirp(claudeHooksDir);

  // Copy the unified Python hook and remove legacy copies
  copyFile(path.join(HOOKS_DIR, "totem-hook.py"), path.join(claudeHooksDir, "totem-hook.py"));
  for (const legacy of ["totem-enforce.py", "totem-store-read.py", "totem-clear-state.py"]) {
    try { fs.unlinkSync(path.join(claudeHooksDir, legacy)); } catch {}
  }

  // Merge settings
  let settings = {};
  if (fs.existsSync(claudeSettingsPath)) {
    try {
      settings = JSON.parse(fs.readFileSync(claudeSettingsPath, "utf-8"));
    } catch {}
  }

  if (!settings.hooks) settings.hooks = {};

  // Hooks are installed to ~/.claude/hooks, so reference that absolute path
  // (the previous ${CLAUDE_PROJECT_DIR}/.claude/hooks only worked for projects
  // that had their own copy).
  const hookDir = claudeHooksDir.replace(/\\/g, "/");
  const hooks = {
    PreToolUse: [
      { matcher: ".*", hooks: [{ type: "command", command: `python3 ${hookDir}/totem-hook.py pre`, timeout: 10 }] },
    ],
    PostToolUse: [
      { matcher: "Read|Edit|Write|MultiEdit|NotebookEdit", hooks: [{ type: "command", command: `python3 ${hookDir}/totem-hook.py post`, timeout: 10 }] },
    ],
    UserPromptSubmit: [
      { hooks: [{ type: "command", command: `python3 ${hookDir}/totem-hook.py clear`, timeout: 5 }] },
    ],
  };

  // Replace any existing totem hook entries (legacy or current), keep others
  for (const [event, entries] of Object.entries(hooks)) {
    const existing = (settings.hooks[event] || []).filter((entry) => {
      const cmds = (entry.hooks || []).map((h) => h.command || "");
      return !cmds.some((c) => c.includes("totem-"));
    });
    settings.hooks[event] = [...existing, ...entries];
  }

  fs.writeFileSync(claudeSettingsPath, JSON.stringify(settings, null, 2) + "\n");
  console.log(`  → ~/.claude/settings.json`);
}

// ── 4. Configure Kimi Code ────────────────────────────────────────

const kimiHome = path.join(os.homedir(), ".kimi-code");
const kimiHooksDir = path.join(kimiHome, "hooks");

if (fs.existsSync(kimiHome) || hasCommand("kimi --version")) {
  console.log("[totem] Configuring Kimi Code...");
  mkdirp(kimiHooksDir);

  copyFile(path.join(HOOKS_DIR, "totem-hook.py"), path.join(kimiHooksDir, "totem-hook.py"));
  for (const legacy of ["totem-enforce.py", "totem-store-read.py", "totem-clear-state.py"]) {
    try { fs.unlinkSync(path.join(kimiHooksDir, legacy)); } catch {}
  }

  // Copy plugin manifest and the hook the manifest references (./hooks/...)
  const kimiPluginDir = path.join(kimiHome, "plugins", "totem-enforce");
  mkdirp(path.join(kimiPluginDir, "hooks"));
  copyFile(path.join(PKG_DIR, "kimi.plugin.json"), path.join(kimiPluginDir, "plugin.json"));
  copyFile(path.join(HOOKS_DIR, "totem-hook.py"), path.join(kimiPluginDir, "hooks", "totem-hook.py"));

  // Register MCP server at user level so totem is available in EVERY project,
  // not just repos with a .mcp.json (project-level config overrides this).
  // Merge with existing servers rather than replacing the file.
  const kimiMcpPath = path.join(kimiHome, "mcp.json");
  try {
    let kimiMcp = { mcpServers: {} };
    if (fs.existsSync(kimiMcpPath)) {
      kimiMcp = JSON.parse(fs.readFileSync(kimiMcpPath, "utf-8"));
      if (!kimiMcp.mcpServers || typeof kimiMcp.mcpServers !== "object") kimiMcp.mcpServers = {};
    }
    const cmd = buildMcpCommand();
    kimiMcp.mcpServers.totem = { command: cmd[0], args: cmd.slice(1) };
    fs.writeFileSync(kimiMcpPath, JSON.stringify(kimiMcp, null, 2) + "\n");
    console.log("  → ~/.kimi-code/mcp.json");
  } catch (e) {
    console.log(`  → Could not update ~/.kimi-code/mcp.json: ${e.message}`);
  }

  // Wire enforcement hooks into ~/.kimi-code/config.toml (idempotent).
  try {
    configureKimiHooks(path.join(kimiHome, "config.toml"));
  } catch (e) {
    console.log(`  → Could not update ~/.kimi-code/config.toml: ${e.message}`);
  }
}

console.log("[totem] Done. Restart your agent for changes to take effect.");
