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
  if (hasCommand("pip --version") || hasCommand("pip3 --version")) {
    const pip = hasCommand("pip --version") ? "pip" : "pip3";
    try {
      execSync(`${pip} install totem-mcp`, { stdio: "inherit", timeout: 120000 });
    } catch {
      console.error(`[totem] ${pip} install failed. Try: pip install totem-mcp`);
      process.exit(1);
    }
  } else if (hasCommand("uvx --version")) {
    try {
      execSync("uvx totem-mcp --version", { stdio: "inherit", timeout: 60000 });
    } catch {
      console.error("[totem] Failed. Try: uvx totem-mcp or pip install totem-mcp");
      process.exit(1);
    }
  } else {
    console.error("[totem] No Python found. Install: pip install totem-mcp");
    process.exit(1);
  }
}

const version = execSync("totem-mcp --version", { encoding: "utf-8", timeout: 5000 }).trim();
console.log(`[totem] ${version}`);

// ── 2. Configure OpenCode ─────────────────────────────────────────

const opencodeDir = path.join(os.homedir(), ".config", "opencode", "plugins");
const opencodeConfigPath = path.join(os.homedir(), ".config", "opencode", "opencode.json");
if (hasCommand("opencode --version")) {
  console.log("[totem] Configuring OpenCode...");
  mkdirp(opencodeDir);
  copyFile(
    path.join(PKG_DIR, ".opencode", "plugins", "totem-enforce.js"),
    path.join(opencodeDir, "totem-enforce.js")
  );

  // Register plugin and MCP server in opencode.json
  if (fs.existsSync(opencodeConfigPath)) {
    try {
      const config = JSON.parse(fs.readFileSync(opencodeConfigPath, "utf-8"));
      let changed = false;

      const pluginEntry = "@emiliano-go/totem";
      if (!config.plugin) config.plugin = [];
      if (!config.plugin.includes(pluginEntry)) {
        config.plugin.push(pluginEntry);
        changed = true;
        console.log(`  → Added ${pluginEntry} to opencode.json`);
      } else {
        console.log(`  → ${pluginEntry} already in opencode.json`);
      }

      if (!config.mcp) config.mcp = {};
      if (!config.mcp.totem) {
        config.mcp.totem = {
          type: "local",
          command: ["uvx", "totem-mcp"],
          enabled: true,
        };
        changed = true;
        console.log(`  → Added totem MCP server to opencode.json`);
      } else {
        console.log(`  → totem MCP server already in opencode.json`);
      }

      if (changed) {
        fs.writeFileSync(opencodeConfigPath, JSON.stringify(config, null, 2) + "\n");
      }
    } catch (e) {
      console.log(`  → Could not update opencode.json: ${e.message}`);
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
    fs.writeFileSync(opencodeConfigPath, JSON.stringify(config, null, 2) + "\n");
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
