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

if (!hasCommand("totem-mcp --version")) {
  console.log("[totem] totem-mcp not found. Installing...");
  let installed = false;

  // Try pipx first (works on externally-managed Python like Arch Linux)
  if (!installed && hasCommand("pipx --version")) {
    try {
      execSync("pipx install totem-mcp", { stdio: "inherit", timeout: 120000 });
      installed = true;
    } catch {}
  }

  // Try uvx (runs in isolated env, no system Python conflict)
  if (!installed && hasCommand("uvx --version")) {
    try {
      execSync("uvx --install totem-mcp", { stdio: "inherit", timeout: 60000 });
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
  if (!installed && !hasCommand("totem-mcp --version")) {
    console.error("[totem] Install failed. Try one of:");
    console.error("  pipx install totem-mcp");
    console.error("  uvx --install totem-mcp");
    console.error("  pip install --user totem-mcp");
    process.exit(1);
  }
}

const version = execSync("totem-mcp --version", { encoding: "utf-8", timeout: 5000 }).trim();
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
      // Strip JSONC comments before parsing
      let raw = fs.readFileSync(opencodeConfigPath, "utf-8");
      raw = raw.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
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
      const mcpCommand = ["uvx", "totem-mcp"];
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
          command: ["uvx", "totem-mcp"],
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

  // Copy Python hooks
  copyFile(path.join(HOOKS_DIR, "totem-enforce.py"), path.join(claudeHooksDir, "totem-enforce.py"));
  copyFile(path.join(HOOKS_DIR, "totem-store-read.py"), path.join(claudeHooksDir, "totem-store-read.py"));
  copyFile(path.join(HOOKS_DIR, "totem-clear-state.py"), path.join(claudeHooksDir, "totem-clear-state.py"));

  // Merge settings
  let settings = {};
  if (fs.existsSync(claudeSettingsPath)) {
    try {
      settings = JSON.parse(fs.readFileSync(claudeSettingsPath, "utf-8"));
    } catch {}
  }

  if (!settings.hooks) settings.hooks = {};

  const hookDir = "${CLAUDE_PROJECT_DIR}/.claude/hooks";
  const hooks = {
    PreToolUse: [
      { matcher: "Grep|Glob", hooks: [{ type: "command", command: `python3 ${hookDir}/totem-enforce.py`, timeout: 10 }] },
      { matcher: "Read", hooks: [{ type: "command", command: `python3 ${hookDir}/totem-enforce.py`, timeout: 10 }] },
      { matcher: "Bash", hooks: [{ type: "command", command: `python3 ${hookDir}/totem-enforce.py`, timeout: 10 }] },
    ],
    PostToolUse: [
      { matcher: "Read", hooks: [{ type: "command", command: `python3 ${hookDir}/totem-store-read.py`, timeout: 15 }] },
    ],
    UserPromptSubmit: [
      { hooks: [{ type: "command", command: `python3 ${hookDir}/totem-clear-state.py`, timeout: 5 }] },
    ],
  };

  // Only add hooks that don't already exist
  for (const [event, entries] of Object.entries(hooks)) {
    if (!settings.hooks[event]) {
      settings.hooks[event] = entries;
    }
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

  copyFile(path.join(HOOKS_DIR, "totem-enforce.py"), path.join(kimiHooksDir, "totem-enforce.py"));
  copyFile(path.join(HOOKS_DIR, "totem-store-read.py"), path.join(kimiHooksDir, "totem-store-read.py"));
  copyFile(path.join(HOOKS_DIR, "totem-clear-state.py"), path.join(kimiHooksDir, "totem-clear-state.py"));

  // Copy plugin manifest
  const kimiPluginDir = path.join(kimiHome, "plugins", "totem-enforce");
  mkdirp(kimiPluginDir);
  copyFile(path.join(PKG_DIR, "kimi.plugin.json"), path.join(kimiPluginDir, "plugin.json"));
}

console.log("[totem] Done. Restart your agent for changes to take effect.");
