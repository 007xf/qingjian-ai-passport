**English** · [简体中文](qingjian-claude.zh_CN.md)

# Claude Code usage in Qingjian 1.5.0

Claude is an independent usage display. Badge evolution still uses Codex current-cycle Tokens plus Cursor account-month Tokens. A missing Claude installation does not block that existing sum.

## Connect and display

1. Sign in to Claude Code normally. Qingjian does not install it, sign you in, read the Keychain directly or start a model task.
2. Open **Data Sources → Claude Code → Configure Status Line**. This explicit action preserves the previous command and its other status-line options, retains unrelated hooks/settings, and saves exact private backups. Repeating setup does not wrap the command again. Settings outside the current home or behind symlinks are rejected.
3. Wait for a normal Claude Code response. The documented `rate_limits` fields are supplied after the first response for supported Claude.ai Pro/Max accounts. Unsupported plans or independently absent windows stay unknown. **Read Usage** reads captured data; it does not request a response to manufacture a fresh observation.
4. Enable **Show Claude Code Usage Separately**, then **Upload to Badge**. With 1.5.0 firmware the optional page works over USB and Bluetooth. Old firmware skips the new command, while Mac readings remain available. Ordinary background sync does not upload unfinished display preferences.

The observer forwards the original status-line input bytes and previous shell command, including its output and exit status. Its quota capture writes only selected numbers/times and anonymous scope hashes under Qingjian's own state directory. Moving the app updates an already installed runtime mapping; it does not install configuration silently.

## Recorded Tokens

The current `CLAUDE_CONFIG_DIR`, or the standard `.claude` directory, supplies `projects/**/*.jsonl`, including subagent transcripts. Each assistant API call contributes:

```text
input_tokens + output_tokens + cache_creation_input_tokens + cache_read_input_tokens
```

The cache-creation breakdown is not added again. API message/request aliases identify one call across repeated content blocks, streaming updates, forks and copies. Separate subagent calls count separately. Synthetic/error messages and user content are excluded. The badge shows the recorded local day's count and recorded local history, with their original update time.

This is a local-profile transcript count, potentially spanning historical accounts. It is not a bill, a whole-account total, a cross-device total or remaining allowance. Source versions may omit or understate fields; Qingjian does not infer missing Tokens from cost or quota percentages. An incomplete scan keeps a last complete observation or reports unknown. Only anonymous counters and index progress are cached; prompts, responses and original IDs are not persisted.

Large archives continue across refreshes instead of starting over when the scan budget is exhausted. Unchanged indexed logs are not parsed again; appended, replaced, edited and deleted sources are reconciled before a new complete observation. Large user-image records can be skipped in bounded chunks. Oversized assistant records or unknown record types remain incomplete, because their usage cannot be safely assumed absent. The first complete reading may therefore take several refreshes.

## Quota observations and identity

The documented Claude Code `statusLine` fields supply each window's `used_percentage` and `resets_at`. Qingjian does not use a private OAuth usage endpoint. It invokes the documented, non-model **`claude auth status`** metadata command to check the current CLI scope; the returned email/organization is hashed and discarded. Modern `configDirectory` identity metadata requires Claude Code 2.1.268 or later. Missing CLI or identity fields prevent a cached reading from being presented as current.

Even a matching current CLI identity cannot prove the identity of an already-running old session. The UI therefore keeps that attribution limitation explicit. A session cannot be rebound to another known account, or upgraded from an unknown identity, merely by rerendering its status line. Start a new session after changing accounts. A known account change or sign-out clears the old device quota; an uncertain identity may retain only a last reading with its original time.

Repeated identical observations do not extend the original quota expiry. Quota freshness is capped at three minutes and at the applicable reset. Tokens and quota have separate clocks. Expired readings remain labelled last readings; future times are not trusted. Local refresh failures never become a newly observed zero. Neither source changes the existing growth total.

## Validation and references

Anonymous host fixtures cover source/profile isolation, streaming and alias deduplication, bounded scanning, original-command preservation, private backups/rollback, exact Python-to-C payload parsing, sign-out/account-change clearing, old-firmware compatibility and display expiry. Firmware builds, packaged-Mac verification and physical acceptance are reported separately in [release validation](RELEASE-VALIDATION.md). This update has no new physical-device or real-account acceptance claim.

- [Official status-line schema and rate-limit availability](https://code.claude.com/docs/en/statusline)
- [Official CLI authentication status](https://code.claude.com/docs/en/cli-reference)
- [Official per-message usage and shared API message IDs](https://code.claude.com/docs/en/agent-sdk/python)
- [Official cache-token counting](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
