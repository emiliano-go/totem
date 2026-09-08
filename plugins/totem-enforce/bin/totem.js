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

// ── 1. Install totem-mcp ──────────────────────────────────────────

if (!hasCommand("totem --version")) {
  console.log("[totem] totem-mcp not found. Installing...");
  let installed = false;

  // Try pipx first (works on externally-managed Python like Arch Linux)
  if (!installed && hasCommand("pipx --version")) {
    try {
      execSync("pipx install totem-mcp", { stdio: "inherit", timeout: 120000 });
      installed = true;
    } catch {}
  }

  // Try uv tool (runs in isolated env, no system Python conflict)
  if (!installed && hasCommand("uv --version")) {
    try {
      execSync("uv tool install totem-mcp", { stdio: "inherit", timeout: 120000 });
      installed = true;
    } catch {}
  }

  // Try pip (may fail on externally-managed environments)
  if (!installed && (hasCommand("pip --version") || hasCommand("pip3 --version"))) {
    const pip = hasCommand("pip --version") ? "pip" : "pip3";
    try {
      execSync(`${pip} install --user totem-mcp`, { stdio: "inherit", timeout: 120000 });
      installed = true;
    } catch {}
  }

  // Check if it became available (e.g. already installed via pipx/uvx)
  if (!installed && !hasCommand("totem --version")) {
    console.error("[totem] Install failed. Try one of:");
    console.error("  pipx install totem-mcp");
    console.error("  uvx --install totem-mcp");
    console.error("  pip install --user totem-mcp");
    process.exit(1);
  }
}

const version = execSync("totem --version", { encoding: "utf-8", timeout: 5000 }).trim();
console.log(`[totem] ${version}`);

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
      // Strip JSONC comments before parsing — but only outside string
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
      const mcpCommand = ["totem-mcp"];
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
          command: ["totem-mcp"],
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
}

console.log("[totem] Done. Restart your agent for changes to take effect.");
