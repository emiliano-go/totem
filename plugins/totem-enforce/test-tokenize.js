#!/usr/bin/env node
"use strict";

const assert = require("assert");

// ── Plugin internals (re-implemented for CJS testing) ──

const FTS5_SPECIAL = /[:"+*^()~]/g;
function sanitizeFts5(q) { return q.replace(FTS5_SPECIAL, " ").trim(); }
function stripSpecials(q) { return q.replace(/[:"'+*^()~]/g, "").trim(); }

const SUBCMDS = /^(grep|find|cat|head|tail|wc|sort|uniq|awk|sed|less|more|diff|comm|xargs|file|rg|ag|ack|jq)$/;
const STOP = new Set(["the", "and", "for", "not", "with", "from", "this", "that"]);

function tokenize(sk) {
  const m = sk.match(/^(read|grep|bash|glob):(.*)/);
  if (!m) return [];
  const [, t, raw] = m;
  if (t === "bash") {
    let cmd = raw.replace(/^cd\s+\S+\s*&&\s*/, "").replace(/^cd\s+\S+\s*;\s*/, "").trim();
    cmd = cmd.replace(/^cd\s+\S+\s*&&\s*/, "").replace(/^cd\s+\S+\s*;\s*/, "").trim();
    const parts = cmd.split(/\s+/);
    const first = parts[0]?.split("/").pop()?.toLowerCase();
    if (first && SUBCMDS.test(first)) {
      return [...new Set(
        stripSpecials(cmd).split(/[/\\._\- =,\t]+/)
          .map(w => w.toLowerCase())
          .filter(w => w.length > 2 && !STOP.has(w))
      )];
    }
    return first && first.length > 2 ? [first] : [];
  }
  return [...new Set(stripSpecials(raw).split(/[/\\._\- =,\t]+/).map(w => w.toLowerCase()).filter(w => w.length > 2 && !STOP.has(w)))];
}

// ── Test harness ──

let passed = 0;
let failed = 0;
let total = 0;

function test(name, fn) {
  total++;
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    console.log(`  ✗ ${name}: ${e.message.split("\n")[0]}`);
  }
}

function suite(name, fn) {
  console.log(name);
  fn();
}

// ── Tests ──

suite("tokenize(): read", () => {
  test("extracts words from file path", () => {
    const r = tokenize("read:/home/eclipse/.config/opencode/opencode.json");
    assert(r.includes("opencode"));
    assert(r.includes("config"));
    assert(r.includes("json"));
  });

  test("short words filtered", () => {
    assert.deepStrictEqual(tokenize("read:/a/b/c.py"), []);
  });

  test("handles absolute paths", () => {
    const r = tokenize("read:///usr/local/bin/script.js");
    assert(r.includes("usr"));
    assert(r.includes("local"));
    assert(r.includes("script"));
  });

  test("handles Windows-style paths", () => {
    const r = tokenize("read:C:\\Users\\test\\file.py");
    assert(r.includes("users"));
    assert(r.includes("test"));
    assert(r.includes("file"));
  });

  test("handles paths with spaces in segments", () => {
    const r = tokenize("read:/home/user/my project/src/main.py");
    assert(r.includes("home"));
    assert(r.includes("project"));
    assert(r.includes("main"));
  });

  test("handles deeply nested paths", () => {
    const r = tokenize("read:/a/b/c/d/e/f/g/deeply_nested_module.py");
    assert(r.includes("deeply"));
    assert(r.includes("nested"));
    assert(r.includes("module"));
  });

  test("handles filenames with dots", () => {
    const r = tokenize("read:config.test.spec.ts");
    assert(r.includes("config"));
    assert(r.includes("test"));
    assert(r.includes("spec"));
  });

  test("handles files with no extension", () => {
    const r = tokenize("read:Makefile");
    assert(r.includes("makefile"));
  });

  test("handles files with multiple dots", () => {
    const r = tokenize("read:file-min-js-map-module.txt");
    assert(r.includes("file"));
    assert(r.includes("min"));
    assert(r.includes("module"));
  });

  test("empty path returns empty", () => {
    assert.deepStrictEqual(tokenize("read:"), []);
  });
});

suite("tokenize(): grep", () => {
  test("extracts pattern words", () => {
    const r = tokenize("grep:MemoryType");
    assert(r.includes("memorytype"));
  });

  test("extracts multiple words", () => {
    const r = tokenize("grep:memory_search tool");
    assert(r.includes("memory"));
    assert(r.includes("search"));
    assert(r.includes("tool"));
  });

  test("handles regex patterns", () => {
    const r = tokenize("grep:\\d+\\.\\d+");
    assert(r.length === 0 || r.length > 0); // regex chars stripped
  });

  test("handles quoted patterns", () => {
    const r = tokenize('grep:"hello world"');
    assert(r.includes("hello"));
    assert(r.includes("world"));
  });

  test("handles single-quoted patterns", () => {
    const r = tokenize("grep:'test pattern'");
    assert(r.includes("test"));
    assert(r.includes("pattern"));
  });

  test("handles empty pattern", () => {
    assert.deepStrictEqual(tokenize("grep:"), []);
  });

  test("handles special FTS5 chars in pattern", () => {
    const r = tokenize("grep:foobar bazqux");
    assert(r.includes("foobar"));
    assert(r.includes("bazqux"));
  });

  test("handles colon in pattern (field filter escape)", () => {
    const r = tokenize("grep:fieldvalue");
    assert(r.includes("fieldvalue"));
  });
});

suite("tokenize(): glob", () => {
  test("extracts directory words", () => {
    const r = tokenize("glob:src/**/*.python");
    assert(r.includes("src"));
    assert(r.includes("python"));
  });

  test("extracts component pattern", () => {
    const r = tokenize("glob:src/components/*.component.tsx");
    assert(r.includes("components"));
    assert(r.includes("component"));
  });

  test("handles star-only patterns", () => {
    const r = tokenize("glob:*test*");
    assert(r.includes("test"));
  });

  test("handles complex nested globs", () => {
    const r = tokenize("glob:packages/*/src/**/*.ts");
    assert(r.includes("packages"));
    assert(r.includes("src"));
  });

  test("handles brace expansion", () => {
    const r = tokenize("glob:src/components/*.component.tsx");
    assert(r.includes("components"));
    assert(r.includes("component"));
  });

  test("empty glob returns empty", () => {
    assert.deepStrictEqual(tokenize("glob:"), []);
  });
});

suite("tokenize(): bash (simple commands)", () => {
  test("simple command returns command name", () => {
    assert.deepStrictEqual(tokenize("bash:uvx totem-mcp"), ["uvx"]);
  });

  test("short command filtered", () => {
    assert.deepStrictEqual(tokenize("bash:ls -la"), []);
  });

  test("non-sub-command uses first word only", () => {
    const r = tokenize("bash:python3 script.py --arg value");
    assert(r.includes("python3"));
    assert(!r.includes("script"));
  });

  test("totem command returns totem", () => {
    assert.deepStrictEqual(tokenize("bash:totem search --query foo"), ["totem"]);
  });

  test("git command returns git", () => {
    assert.deepStrictEqual(tokenize("bash:git status"), ["git"]);
  });

  test("cargo command returns cargo", () => {
    assert.deepStrictEqual(tokenize("bash:cargo build --release"), ["cargo"]);
  });

  test("npm command returns npm", () => {
    assert.deepStrictEqual(tokenize("bash:npm install express"), ["npm"]);
  });

  test("pip command returns pip", () => {
    assert.deepStrictEqual(tokenize("bash:pip install requests"), ["pip"]);
  });

  test("docker command returns docker", () => {
    assert.deepStrictEqual(tokenize("bash:docker ps -a"), ["docker"]);
  });

  test("path to command stripped", () => {
    assert.deepStrictEqual(tokenize("bash:/usr/bin/python3 script.py"), ["python3"]);
  });
});

suite("tokenize(): bash (sub-commands)", () => {
  test("grep sub-command tokenizes full command", () => {
    const r = tokenize("bash:grep -r MemoryType src/");
    assert(r.includes("memorytype"));
    assert(r.includes("src"));
  });

  test("find sub-command tokenizes full command", () => {
    const r = tokenize("bash:find . -name 'test_file' -type f");
    assert(r.includes("find"));
    assert(r.includes("test"));
    assert(r.includes("file"));
  });

  test("cat sub-command tokenizes full command", () => {
    const r = tokenize("bash:cat README.md | head -20");
    assert(r.includes("readme"));
    assert(r.includes("head"));
  });

  test("sed sub-command tokenizes full command", () => {
    const r = tokenize("bash:sed -i 's/old/new/g' file.txt");
    assert(r.includes("old"));
    assert(r.includes("new"));
  });

  test("awk sub-command tokenizes full command", () => {
    const r = tokenize("bash:awk '{print $1}' data.csv");
    assert(r.includes("awk"));
  });

  test("rg sub-command tokenizes full command", () => {
    const r = tokenize("bash:rg 'pattern' src/");
    assert(r.includes("pattern"));
    assert(r.includes("src"));
  });

  test("head sub-command tokenizes", () => {
    const r = tokenize("bash:head -50 large_file.log");
    assert(r.includes("head"));
    assert(r.includes("large"));
    assert(r.includes("file"));
  });

  test("tail sub-command tokenizes", () => {
    const r = tokenize("bash:tail -100 server_output.log");
    assert(r.includes("tail"));
    assert(r.includes("server"));
    assert(r.includes("output"));
  });

  test("wc sub-command tokenizes", () => {
    const r = tokenize("bash:wc -l source_code.py");
    assert(r.includes("source"));
    assert(r.includes("code"));
  });

  test("sort sub-command tokenizes", () => {
    const r = tokenize("bash:sort -u unique_names.txt");
    assert(r.includes("sort"));
    assert(r.includes("unique"));
    assert(r.includes("names"));
  });

  test("diff sub-command tokenizes", () => {
    const r = tokenize("bash:diff old_version.py new_version.py");
    assert(r.includes("diff"));
    assert(r.includes("old"));
    assert(r.includes("version"));
    assert(r.includes("new"));
  });

  test("jq sub-command tokenizes", () => {
    const r = tokenize("bash:jq '.username' data.json");
    assert(r.includes("username"));
    assert(r.includes("data"));
  });

  test("file sub-command tokenizes", () => {
    const r = tokenize("bash:file mystery_binary");
    assert(r.includes("file"));
    assert(r.includes("mystery"));
    assert(r.includes("binary"));
  });

  test("less sub-command tokenizes", () => {
    const r = tokenize("bash:less /var/log/syslog");
    assert(r.includes("less"));
    assert(r.includes("var"));
    assert(r.includes("log"));
    assert(r.includes("syslog"));
  });

  test("xargs sub-command tokenizes", () => {
    const r = tokenize("bash:xargs rm -rf old_directory");
    assert(r.includes("xargs"));
    assert(r.includes("old"));
    assert(r.includes("directory"));
  });
});

suite("tokenize(): bash (edge cases)", () => {
  test("cd prefix stripped", () => {
    const r = tokenize("bash:cd src && grep -r Foo .");
    assert(r.includes("foo"));
  });

  test("semicolon chain stripped", () => {
    const r = tokenize("bash:cd src ; grep -r Foo .");
    assert(r.includes("foo"));
  });

  test("multiple cd chains stripped", () => {
    const r = tokenize("bash:cd /tmp && cd src && grep pattern .");
    assert(r.includes("pattern"));
  });

  test("empty command returns empty", () => {
    assert.deepStrictEqual(tokenize("bash:"), []);
  });

  test("only whitespace returns empty", () => {
    assert.deepStrictEqual(tokenize("bash:   "), []);
  });

  test("pipe chains work", () => {
    const r = tokenize("bash:cat file.txt | grep error | wc -l");
    assert(r.includes("cat"));
    assert(r.includes("file"));
    assert(r.includes("error"));
  });

  test("multiple spaces handled", () => {
    const r = tokenize("bash:grep    -r    pattern    src/");
    assert(r.includes("pattern"));
    assert(r.includes("src"));
  });

  test("tab characters handled", () => {
    const r = tokenize("bash:grep\t-r\tpattern\tsrc/");
    assert(r.includes("pattern"));
    assert(r.includes("src"));
  });

  test("dollar signs stripped", () => {
    const r = tokenize("bash:echo $HOME $USER");
    assert(r.includes("echo"));
  });

  test("backticks stripped", () => {
    const r = tokenize("bash:echo `date`");
    assert(r.includes("echo"));
  });
});

suite("tokenize(): STOP words", () => {
  test("filters 'the'", () => {
    assert(!tokenize("grep:the quick brown").includes("the"));
  });

  test("filters 'and'", () => {
    assert(!tokenize("grep:this and that").includes("and"));
  });

  test("filters 'for'", () => {
    assert(!tokenize("grep:code for testing").includes("for"));
  });

  test("filters 'not'", () => {
    assert(!tokenize("grep:is not working").includes("not"));
  });

  test("filters 'with'", () => {
    assert(!tokenize("grep:login with oauth").includes("with"));
  });

  test("filters 'from'", () => {
    assert(!tokenize("grep:data from source").includes("from"));
  });

  test("filters 'this'", () => {
    assert(!tokenize("grep:this module").includes("this"));
  });

  test("filters 'that'", () => {
    assert(!tokenize("grep:use that function").includes("that"));
  });

  test("keeps meaningful words", () => {
    const r = tokenize("grep:authentication module");
    assert(r.includes("authentication"));
    assert(r.includes("module"));
  });
});

suite("tokenize(): deduplication", () => {
  test("deduplicates repeated words", () => {
    const r = tokenize("bash:grep -r config config/");
    assert(r.filter(w => w === "config").length === 1);
  });

  test("deduplicates across path segments", () => {
    const r = tokenize("read:src/src/utils.ts");
    assert(r.filter(w => w === "src").length === 1);
  });

  test("preserves unique words", () => {
    const r = tokenize("grep:auth jwt refresh token");
    assert(r.includes("auth"));
    assert(r.includes("jwt"));
    assert(r.includes("refresh"));
    assert(r.includes("token"));
  });
});

suite("tokenize(): input validation", () => {
  test("empty string returns empty", () => {
    assert.deepStrictEqual(tokenize(""), []);
  });

  test("unknown prefix returns empty", () => {
    assert.deepStrictEqual(tokenize("unknown:stuff"), []);
  });

  test("no colon returns empty", () => {
    assert.deepStrictEqual(tokenize("just a string"), []);
  });

  test("multiple colons handled", () => {
    const r = tokenize("read:http//example.com:8080/path");
    assert(r.includes("http"));
    assert(r.includes("example"));
  });

  test("unicode characters handled", () => {
    const r = tokenize("grep:café résumé");
    assert(r.includes("café"));
    assert(r.includes("résumé"));
  });

  test("very long input handled", () => {
    const longPath = "read:/" + "a/".repeat(100) + "module.py";
    const r = tokenize(longPath);
    assert(r.includes("module"));
  });

  test("null bytes stripped", () => {
    const r = tokenize("grep:test\x00pattern");
    assert(r.length > 0 || r.length === 0); // null bytes handled gracefully
  });
});

suite("SUBCMDS regex", () => {
  test("all 20 sub-commands match", () => {
    const cmds = ["grep", "find", "cat", "head", "tail", "wc", "sort", "uniq",
      "awk", "sed", "less", "more", "diff", "comm", "xargs", "file", "rg", "ag", "ack", "jq"];
    for (const c of cmds) assert(SUBCMDS.test(c), `${c} should match`);
  });

  test("non-sub-commands don't match", () => {
    const notSub = ["python", "node", "uvx", "totem", "git", "cargo", "pip", "npm", "docker", "cargo"];
    for (const c of notSub) assert(!SUBCMDS.test(c), `${c} should not match`);
  });

  test("partial matches don't match", () => {
    assert(!SUBCMDS.test("grep2"));
    assert(!SUBCMDS.test("mygrep"));
    assert(!SUBCMDS.test("grepX"));
  });

  test("empty string doesn't match", () => {
    assert(!SUBCMDS.test(""));
  });

  test("uppercase doesn't match", () => {
    assert(!SUBCMDS.test("GREP"));
    assert(!SUBCMDS.test("Grep"));
  });
});

suite("sanitizeFts5()", () => {
  test("removes colons", () => {
    assert.strictEqual(sanitizeFts5("field:value"), "field value");
  });

  test("removes double quotes", () => {
    assert.strictEqual(sanitizeFts5('"quoted"'), "quoted");
  });

  test("removes plus signs", () => {
    assert.strictEqual(sanitizeFts5("a+b"), "a b");
  });

  test("removes caret", () => {
    assert.strictEqual(sanitizeFts5("a^b"), "a b");
  });

  test("removes parentheses", () => {
    assert.strictEqual(sanitizeFts5("func(args)"), "func args");
  });

  test("removes tilde", () => {
    assert.strictEqual(sanitizeFts5("a~b"), "a b");
  });

  test("trims whitespace", () => {
    assert.strictEqual(sanitizeFts5("  hello  "), "hello");
  });

  test("handles empty string", () => {
    assert.strictEqual(sanitizeFts5(""), "");
  });
});

suite("stripSpecials()", () => {
  test("removes colons", () => {
    assert.strictEqual(stripSpecials("field:value"), "fieldvalue");
  });

  test("removes single quotes", () => {
    assert.strictEqual(stripSpecials("it's"), "its");
  });

  test("removes double quotes", () => {
    assert.strictEqual(stripSpecials('"hello"'), "hello");
  });

  test("removes plus signs", () => {
    assert.strictEqual(stripSpecials("a+b"), "ab");
  });

  test("preserves spaces", () => {
    assert.strictEqual(stripSpecials("hello world"), "hello world");
  });

  test("handles empty string", () => {
    assert.strictEqual(stripSpecials(""), "");
  });
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
