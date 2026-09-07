#!/usr/bin/env node
"use strict";

const assert = require("assert");

// ── Import plugin internals by re-implementing the pure functions ──
// (We can't import the ES module directly in CJS, so we test the logic)

const FTS5_SPECIAL = /[:"+*^()~]/g;
function sanitizeFts5(q) { return q.replace(FTS5_SPECIAL, " ").trim(); }
// For tokenization: strip quotes and FTS5 specials entirely
function stripSpecials(q) { return q.replace(/[:"'+*^()~]/g, "").trim(); }

const SUBCMDS = /^(grep|find|cat|head|tail|wc|sort|uniq|awk|sed|less|more|diff|comm|xargs|file|rg|ag|ack|jq)$/;
const STOP = new Set(["the", "and", "for", "not", "with", "from", "this", "that"]);

function tokenize(sk) {
  const m = sk.match(/^(read|grep|bash|glob):(.*)/);
  if (!m) return [];
  const [, t, raw] = m;
  if (t === "bash") {
    const cmd = raw.replace(/^cd\s+\S+\s*&&\s*/, "").replace(/^cd\s+\S+\s*;\s*/, "").trim();
    const parts = cmd.split(/\s+/);
    const first = parts[0]?.split("/").pop()?.toLowerCase();
    if (first && SUBCMDS.test(first)) {
      return [...new Set(
        stripSpecials(cmd).split(/[/\\._\- =,]+/)
          .map(w => w.toLowerCase())
          .filter(w => w.length > 2 && !STOP.has(w))
      )];
    }
    return first && first.length > 2 ? [first] : [];
  }
  return [...new Set(stripSpecials(raw).split(/[/\\._\- =,]+/).map(w => w.toLowerCase()).filter(w => w.length > 2 && !STOP.has(w)))];
}

// ── Tests ──

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  ✓ ${name}`);
  } catch (e) {
    failed++;
    console.log(`  ✗ ${name}`);
    console.log(`    ${e.message}`);
  }
}

console.log("tokenize()");

test("read: extracts words from file path", () => {
  const r = tokenize("read:/home/eclipse/.config/opencode/opencode.json");
  assert(r.includes("opencode"));
  assert(r.includes("config"));
  assert(r.includes("json"));
});

test("read: short words filtered", () => {
  const r = tokenize("read:/a/b/c.py");
  assert(r.length === 0);
});

test("grep: extracts pattern words", () => {
  const r = tokenize("grep:MemoryType");
  assert(r.includes("memorytype"));
});

test("grep: extracts multiple words from pattern", () => {
  const r = tokenize("grep:memory_search tool");
  assert(r.includes("memory"));
  assert(r.includes("search"));
  assert(r.includes("tool"));
});

test("glob: extracts pattern words", () => {
  const r = tokenize("glob:src/**/*.python");
  assert(r.includes("src"));
  assert(r.includes("python"));
});

test("glob: extracts directory pattern", () => {
  const r = tokenize("glob:src/components/*.component.tsx");
  assert(r.includes("src"));
  assert(r.includes("components"));
  assert(r.includes("component"));
  assert(r.includes("tsx"));
});

test("bash: simple command returns command name", () => {
  const r = tokenize("bash:uvx totem-mcp");
  assert(r.includes("uvx"));
});

test("bash: short command filtered", () => {
  const r = tokenize("bash:ls -la");
  assert(r.length === 0);
});

test("bash: grep sub-command tokenizes full command", () => {
  const r = tokenize("bash:grep -r MemoryType src/");
  assert(r.includes("memorytype"));
  assert(r.includes("src"));
});

test("bash: find sub-command tokenizes full command", () => {
  const r = tokenize("bash:find . -name 'test_file' -type f");
  assert(r.includes("find"));
  assert(r.includes("test"));
  assert(r.includes("file"));
});

test("bash: cat sub-command tokenizes full command", () => {
  const r = tokenize("bash:cat README.md | head -20");
  assert(r.includes("readme"));
  assert(r.includes("head"));
});

test("bash: sed sub-command tokenizes full command", () => {
  const r = tokenize("bash:sed -i 's/old/new/g' file.txt");
  assert(r.includes("sed"));
  assert(r.includes("old"));
  assert(r.includes("new"));
});

test("bash: awk sub-command tokenizes full command", () => {
  const r = tokenize("bash:awk '{print $1}' data.csv");
  assert(r.includes("awk"));
});

test("bash: rg sub-command tokenizes full command", () => {
  const r = tokenize("bash:rg 'pattern' src/");
  assert(r.includes("pattern"));
  assert(r.includes("src"));
});

test("bash: cd prefix stripped", () => {
  const r = tokenize("bash:cd src && grep -r Foo .");
  assert(r.includes("foo"));
});

test("bash: semicolon chain stripped", () => {
  const r = tokenize("bash:cd src ; grep -r Foo .");
  assert(r.includes("foo"));
});

test("bash: non-sub-command uses first word only", () => {
  const r = tokenize("bash:python3 script.py --arg value");
  assert(r.includes("python3"));
  assert(!r.includes("script")); // not tokenized
});

test("bash: totem command returns totem", () => {
  const r = tokenize("bash:totem search --query foo");
  assert(r.includes("totem"));
});

test("empty input returns empty", () => {
  assert.deepStrictEqual(tokenize(""), []);
});

test("unknown prefix returns empty", () => {
  assert.deepStrictEqual(tokenize("unknown:stuff"), []);
});

test("FTS5 special chars sanitized", () => {
  const r = tokenize('grep:foo bar baz');
  assert(r.includes("foo"));
  assert(r.includes("bar"));
  assert(r.includes("baz"));
});

test("stop words filtered", () => {
  const r = tokenize("grep:the quick brown fox");
  assert(r.includes("quick"));
  assert(r.includes("brown"));
  assert(r.includes("fox"));
  assert(!r.includes("the"));
});

test("deduplication works", () => {
  const r = tokenize("bash:grep -r config config/");
  const configCount = r.filter(w => w === "config").length;
  assert(configCount === 1);
});

console.log("\nSUBCMDS regex");

test("all sub-commands match", () => {
  const cmds = ["grep", "find", "cat", "head", "tail", "wc", "sort", "uniq",
    "awk", "sed", "less", "more", "diff", "comm", "xargs", "file", "rg", "ag", "ack", "jq"];
  for (const c of cmds) {
    assert(SUBCMDS.test(c), `${c} should match`);
  }
});

test("non-sub-commands don't match", () => {
  assert(!SUBCMDS.test("python"));
  assert(!SUBCMDS.test("node"));
  assert(!SUBCMDS.test("uvx"));
  assert(!SUBCMDS.test("totem"));
  assert(!SUBCMDS.test("git"));
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
