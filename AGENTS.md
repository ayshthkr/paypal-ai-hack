<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## Repo continuity notes

- Read `handoff.md` for current project status and continuation context; use `README.md` and `walkthrough.md` for setup and UI behavior.
- Before writing Next.js code, read the relevant version-specific guide under `node_modules/next/dist/docs/` and follow it. Preserve the generated Next.js block above.
- Keep PayPal operations in Sandbox mode and respect the controlled recipient allowlist. `make seed-sandbox` sends Sandbox invoices; `make record-payment` records a Sandbox external payment.
- Keep credentials in ignored environment files or hosting settings. Never print, commit, or copy secrets into docs/chat. Gemini is used for grounded reminder drafts and reply classification, with a deterministic fallback.
- Check `git status` before changes and preserve existing uncommitted work.
- For the Render API wake-up issue, dashboard retries are 30 × 2.5 seconds and failed sibling reads are aborted. Recent commits `9e1359d`, `11989f0`, and `a7af8e7` improve portfolio read batching/caching and cancellation; latest live proxy reads returned 200 in about 12–13 seconds after wake-up. See `handoff.md` for current deployment evidence and limits.
