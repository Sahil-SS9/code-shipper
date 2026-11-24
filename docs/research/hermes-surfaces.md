# Verified host surfaces

## Hermes Agent

Pinned: NousResearch/hermes-agent `upstream/main` = `4f676af2cfee3fb41b2047bccd2dfa39dbd73bd7`
(24/11/2026, tag line `rc.2-v0.23.4`). Verify with an ancestry check, not equality:
`git merge-base --is-ancestor 4f676af2cfee3fb41b2047bccd2dfa39dbd73bd7 upstream/main; echo $?` → 0.

| Surface | Signature / fact | Source path |
|---------|------------------|-------------|
| Slash command | `ctx.register_command(name, handler, description="", args_hint="", argument_mode=None)`; handler `fn(raw_args) -> str | None`; refused if it clashes with a built-in | `hermes_cli/plugins_context.py` |
| CLI subcommand | `ctx.register_cli_command(name, help, setup_fn, handler_fn=None, description="")` → `hermes <name> ...` | same |
| Tool | `ctx.register_tool(name, toolset, schema, handler, ...)` | same |
| Hooks | `ctx.register_hook(name, cb)`; `VALID_HOOKS` includes `pre_tool_call`, `post_tool_call`, `pre_approval_request`, ... | `hermes_cli/plugins.py` |
| LLM from a plugin | `ctx.llm.complete(...)`, `ctx.llm.complete_structured(...)`; host-owned auth, fail-closed trust gate | `website/docs/developer-guide/plugin-llm-access.md` |
| Plugin storage | `plugins.plugin_storage.plugin_data_dir(name)` → `<hermes home>/plugin-data/<name>/` | `plugins/plugin_storage.py` |
| Public imports | Only modules listed in the public plugin API; `hermes plugins validate` warns on others | `website/docs/developer-guide/plugins/public-api.md` |
| YAML | `hermes_yaml.safe_load` / `safe_dump` are public | same |
| Cron run identity | `ctx.current_cron_execution()` | `website/docs/developer-guide/plugins/index.md` |
| Script-only cron | Script runs on schedule, stdout delivered, no LLM | `website/docs/guides/cron-script-only.md` |
| One-shot CLI | `hermes chat --query-file - --oneshot -Q --source tool -p <profile>` | `website/docs/reference/cli-commands.md` |
| Prior GitHub PR webhook guide | Webhook platform route for PR comments | `website/docs/guides/webhook-github-pr-review.md` |

Note: `hermes mcp serve` has moved out of core into a catalog plugin (reported by the discovery
run; not used by v1).

## Claude Ship reference docs (behaviour source, not code)

- https://claude.com/resources/articles/introducing-claude-ship
- https://code.claude.com/docs/en/claude-ship
- https://code.claude.com/docs/en/code-review
- https://code.claude.com/docs/en/pr-steward and /pr-steward-security
- https://code.claude.com/docs/en/approvals-policy
- https://code.claude.com/docs/en/ship-command

Clean-room rule: Code-Shipper reimplements behaviour described in public docs. Do not copy
Anthropic's text, policy schema verbatim, or prompts.
