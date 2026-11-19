// What every script here that node runs does the same on its command line (dev/cli.py, for node), so each answers as a
// command line is expected to (GNU's options, POSIX's exit codes: the Command line checks hold them,
// tests/callboard_clichecks):
//
//     -h, --help      the script's usage, on stdout: exit 0
//     --version       the script's name and the project's version, on stdout: exit 0
//     an option or a subcommand it doesn't have: said on stderr, nothing on stdout, exit 2
//
// A script calls front first, before it loads anything that might not be installed (Playwright), and goes on when it
// comes back:
//
//     require("./cli.js").front(args, "node dev/icons.js",       // (args: its own command line) "The app's icons, drawn from its logos.",
//                               ["node dev/icons.js           # make them", "node dev/icons.js check     # whether they're what would be made"],
//                               { commands: ["check"] });
"use strict";
const fs = require("fs"), path = require("path");

const MISUSE = 2;

// The project's version (plexcache/__init__.py's __version__), read.
function version() {
  try {
    const m = /^__version__\s*=\s*["']([^"']+)["']/m.exec(fs.readFileSync(path.join(__dirname, "..", "plexcache", "__init__.py"), "utf-8"));
    return m ? m[1] : "unknown";
  } catch (e) {
    return "unknown";
  }
}

function usage(prog, about, lines) {
  const width = Math.max(prog.length + 14, 20);
  return [about, "", ...lines.map((l) => "  " + l),
    "  " + (prog + " -h, --help").padEnd(width) + "   # this help",
    "  " + (prog + " --version").padEnd(width) + "   # the version"].join("\n");
}

// Help and the version answered and a command line the script can't read refused (the process ends: 0 or 2); it comes
// back where it's the script's to go on with. how.options: the options it takes; how.commands: its subcommands (none
// given: it has none, and what isn't an option is its own to read). args: the script's command line.
function front(args, prog, about, lines, how) {
  const { options = [], commands = null } = how || {};
  const name = prog.split(" ").pop();
  const end = (code, text) => { fs.writeSync(code ? 2 : 1, text + "\n"); process.exit(code); };
  if (args.includes("-h") || args.includes("--help") || (commands && args[0] === "help")) end(0, usage(prog, about, lines));
  if (args.includes("--version")) end(0, name + " " + version());
  for (const a of args) {
    if (a.startsWith("-") && a !== "-" && !options.includes(a)) end(MISUSE, `${name}: unknown option: ${a} (see ${prog} --help)`);
  }
  if (commands && args.length && !commands.includes(args[0])) {
    end(MISUSE, `${name}: unknown command: ${args[0]} (one of: ${commands.join(", ")}; see ${prog} --help)`);
  }
}

module.exports = { front, usage, version, MISUSE };
