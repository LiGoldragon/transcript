# transcript

CLI shim for searching Claude Code harness transcripts. Temporary,
pending a Nexus.

Reads JSONL session files from `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`.
Codex rollouts and Pi sessions are out of scope for now; the shape is open
for them.

## Usage

Run from anywhere with `nix run github:LiGoldragon/transcript -- <command>`.

### show

Print every live-typed user message with preceding assistant context.

```
nix run . -- show 358f143a -n 2 --cap 150
```

### search

Search live-typed messages across transcripts.

```
nix run . -- search "bluffing" --recent 10
```

### raw

Print raw JSONL records at specific line numbers.

```
nix run . -- raw 358f143a 84 222
```
