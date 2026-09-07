#!/usr/bin/env node
"use strict";

const { execSync } = require("child_process");

function hasCommand(cmd) {
  try {
    execSync(cmd, { stdio: "ignore", timeout: 5000 });
    return true;
  } catch {
    return false;
  }
}

// Check if totem-mcp is available
if (!hasCommand("totem-mcp --version")) {
  console.log("[totem] totem-mcp not found. Installing...");

  // Try uvx first (fastest), then pip
  if (hasCommand("uvx --version")) {
    console.log("[totem] Using uvx to install totem-mcp...");
    try {
      execSync("uvx totem-mcp --version", { stdio: "inherit", timeout: 60000 });
      console.log("[totem] totem-mcp installed via uvx.");
    } catch {
      console.error("[totem] Failed to install via uvx. Try: uvx totem-mcp");
      process.exit(1);
    }
  } else if (hasCommand("pip --version") || hasCommand("pip3 --version")) {
    const pip = hasCommand("pip --version") ? "pip" : "pip3";
    console.log(`[totem] Using ${pip} to install totem-mcp...`);
    try {
      execSync(`${pip} install totem-mcp`, { stdio: "inherit", timeout: 120000 });
      console.log("[totem] totem-mcp installed.");
    } catch {
      console.error(`[totem] Failed to install via ${pip}. Try: ${pip} install totem-mcp`);
      process.exit(1);
    }
  } else {
    console.error("[totem] No Python package manager found.");
    console.error("[totem] Install manually: uvx totem-mcp  or  pip install totem-mcp");
    process.exit(1);
  }
}

// Verify installation
try {
  const version = execSync("totem-mcp --version", { encoding: "utf-8", timeout: 5000 }).trim();
  console.log(`[totem] totem-mcp ${version} ready.`);
} catch {
  console.error("[totem] totem-mcp installed but not on PATH. Restart your shell.");
}
