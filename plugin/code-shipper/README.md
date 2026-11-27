# Code-Shipper for Hermes

`/ship` reviews a GitHub pull request, can fix it within a budget, and gets it ready for a person to
merge. Code-Shipper never approves and never merges: a person always merges.

This plugin is a thin wrapper. Every rule (the review, the fixer's container, the secret scan and the
publish gate) lives in the `code-shipper` command-line tool, and the plugin calls it.

## Install

1. Install the CLI from the same repository and put it on your PATH:

   ```
   pipx install git+https://github.com/Sahil-SS9/code-shipper
   code-shipper doctor
   ```

2. Install the plugin:

   ```
   hermes plugins install code-shipper
   ```

3. Use it from a chat:

   ```
   /ship owner/repo 42            review the current head and store the result
   /ship owner/repo 42 board      show the stored review
   /ship owner/repo 42 fix        fix within a budget, in a container, and push to the PR
   /ship owner/repo 42 status     show the effective approvals policy
   ```

Run `/ship` with no arguments for the full list.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `binary` | `code-shipper` | The CLI to call. |
| `default_profile` | `standard` | How much the reviewer reports: `quiet`, `standard` or `assertive`. |
| `allow_ship` | `false` | `/ship ... publish` never publishes from chat. Off: refused. On: it names the terminal command, `code-shipper ship`, which shows the plan and asks first. |

## What it touches

- **Shell commands.** Every `/ship` runs the `code-shipper` CLI as a child process and waits for it,
  for up to 30 minutes (`fix` stops itself after 15 minutes or 3 rounds by default). The child gets a
  short allowlisted environment: `PATH`, home, locale, which Hermes install and profile to use, the
  container runtime settings and Code-Shipper's own state directory. Provider keys, tokens and Hermes's
  run settings are not passed, and it gets no standard input.
- **Agent runs.** The CLI runs Hermes one-shot chats (`hermes chat --oneshot --source tool`) for the
  review. Reading tasks run with no tools. The fixer runs in a rootless container with no GitHub token.
- **Network.** The CLI calls the GitHub API for the pull request you name, and your configured model
  provider through Hermes.
- **Writes to GitHub.** `review` and `board` write nothing. `fix` pushes commits to the pull request's
  branch (at most 3 tries per problem) and adds or removes the `code-shipper-watching` label. `stop`
  removes that label. Nothing approves or merges. `publish` is refused unless you turn on `allow_ship`,
  and even then it sends you to the terminal command.
- **Credentials.** The plugin holds none. The CLI reads its GitHub token from its own file
  (`~/.config/code-shipper/tokens/fixer`) or `CODE_SHIPPER_GITHUB_TOKEN` in the shell where you run it
  directly. It never reads Hermes's `.env` or `auth.json`.
- **Local data.** Review state, audit history (kept 30 days) and logs live under
  `$XDG_STATE_HOME/code-shipper` (default `~/.local/state/code-shipper`).
- **Background work.** None. Polling for new heads (`code-shipper poll`) is a terminal command and is
  not exposed in chat.
- **No self-updating.** The plugin and the CLI never fetch or replace their own code. There is no
  telemetry.

## Licence

MIT.
