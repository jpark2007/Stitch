# Stitch

HackPrinceton Fall 2026 project, due Sunday 2026-11-15. Repo: https://github.com/jpark2007/Stitch

Priorities for all work: **quality > speed > cost.**

Read `README.md` for the product and the relevant section of `ARCHITECTURE.md` before building any part. `DESIGN.md` is the look, feel and interaction spec and the quality bar: read your area's section before building or reviewing any UI, and run its self-testing loop (headless browser, console errors, screenshots, at least two improvement passes) before calling UI work done. `web/AGENTS.md` applies to everything under `web/` (this Next.js version has breaking changes; read its bundled docs before writing code).

## Ownership

Ethan owns the visual experience; Jonah owns the backend and form-and-flow pages, except the agreed Stitch Channels paths owned by Nate below. Stay inside your area; for anything else, post a `REQUEST` on Issue #1.

| Owner | GitHub | Takes | Paths |
|---|---|---|---|
| Jonah | `jpark2007` | Agent + compiler, Data, and the flow pages (sign-up, login, start a family, join, add-a-memory, agent feed) | all of `api/` except Nate's Channels paths below, `web/app/(auth)/`, `web/app/families/`, `web/app/join/`, `web/app/add/`, `web/components/agent/`, `web/lib/api.ts` |
| Ethan | `ethanm9720` | Stage and book, tree home, person and moment pages, threads screen, global look | `web/components/stage/`, `web/components/book/`, `web/components/paper/`, `web/components/tree/`, `web/components/threads/`, `web/app/page.tsx`, `web/app/people/`, `web/app/memories/`, `web/app/render/`, `web/app/threads/`, `web/app/layout.tsx`, `web/app/globals.css`, `web/public/textures/`, web config (`web/package.json`, `next.config.ts`, etc.) |
| Nate | `natek1228` | Stitch Channels: texting, voice interviews, contact and consent settings | `channels/`, `api/channels/`, `api/agent/conversation/`, `api/db/contacts.py`, `api/db/conversations.py`, `api/db/question_bank.py`, `api/tests/channels/`, `api/seed/channels_demo.py`, `web/app/settings/notifications/`, `web/components/channels/` |

- Channels boundaries were agreed on Issue #1 ([Jonah's ACK](https://github.com/jpark2007/Stitch/issues/1#issuecomment-6882196822)). Nate uses existing public functions without editing the rest of the API or flow pages. Jonah owns the Channels mounting/index/purge hooks, shared audio/channel contracts and settings link; Jonah's reviewer must pass the HMAC routes before merge. Ethan owns book playback and teller markers.
- Carsten (`CarstenMacosko`) has no fixed area yet. When picking up work, agree on Issue #1 which paths move and update this table in the same PR.
- `ARCHITECTURE.md` and `DESIGN.md` describe four areas (Stage, Pages, Agent + compiler, Data). Map them with this table: Stage → Ethan; Agent + compiler and Data → Jonah; Pages is divided as listed above.
- To change the split, agree on the issue and update this table in the same PR.

## Contracts

`shared/`, `api/schemas.py`, `web/lib/types.ts` and `web/public/samples/` are contracts used by everyone.

- Only **add** fields. Never rename or remove.
- Post `INTERFACE:` on Issue #1 for every change, with the exact new signature.
- Claim the specific contract file, make the change in a small PR of its own, and post `DONE:` right away so the file is free again.
- `api/schemas.py` and `web/lib/types.ts` mirror each other. Change both in the same PR.

## Git workflow

- One branch per task, sized 1 to 3 hours. Name it `<owner>/<task>`.
- Rebase on `origin/main` before every push.
- **Never push to `main`.** Merge through a PR once checks pass and a reviewer has passed it (see "Working agentically").
- Checks: `web/` → `npx tsc --noEmit && npm run lint`; `api/` → `uv run pytest`.
- **Commits and PRs are authored by the human owner only.** No `Co-Authored-By` trailers and no "Generated with" lines for Claude or any other AI tool, in commit messages or PR descriptions. `.claude/settings.json` turns the automatic ones off.
- Commit as the same GitHub account you push with. Before your first commit, check that `git config user.email` belongs to the account `gh api user -q .login` prints; if not, fix it for this repo with `git config --local user.name` / `user.email`.
- Lockfiles (`web/package-lock.json`, `api/uv.lock`): on conflict take `main`'s version and regenerate (`npm install` / `uv lock`). Never hand-merge.
- Build steps from `ARCHITECTURE.md` section 8 are tracked as GitHub issues labeled `web` / `api`. Reference the issue in your PR.

## Coordination channel
We coordinate through GitHub Issue #1.

- BEFORE starting a task: run `gh issue view 1 --comments` and read the latest messages.
- When you START a task, post: `gh issue comment 1 --body "[<your-name>-agent] CLAIMING: <task>, touching files: <list>"`
- If you CHANGE a shared interface (types, API routes, DB schema), post it immediately with the exact new signature: `gh issue comment 1 --body "[<your-name>-agent] INTERFACE: <exact new signature>"`
- When you FINISH, post: `gh issue comment 1 --body "[<your-name>-agent] DONE: <what you built>, branch/PR: <ref>, notes: <anything others need>"`
- Never edit files another agent has claimed. If you need a change there, post a request instead: `[<your-name>-agent] REQUEST for <owner>: <change>`
- If you are stuck on a decision only a human can make, post `[<your-name>-agent] BLOCKED: <question>` and move to other work.
- Keep messages short. Prefix with your owner's name.

### This is enforced
Hooks in `.claude/settings.json` (script: `.claude/hooks/coord.py`) enforce the protocol in every Claude Code session, including subagents and git worktrees:

- Session start and every prompt: new channel messages and active claims are injected into context.
- Edit/Write is **blocked** until your GitHub user has an active `CLAIMING:` comment, and the file must be covered by its `touching files:` list (comma-separated paths, `dir/` prefixes, or globs). To touch more files, post a new `CLAIMING:` comment; lists accumulate.
- Edit/Write is **blocked** on any file covered by another user's active claim.
- `DONE:` releases **all** of your claims. You must claim again before further edits.
- The keywords `CLAIMING:`, `touching files:`, and `DONE:` are parsed literally. Keep them exact, and do not use them in other kinds of messages.
- Claims are per GitHub user. All agents running under one person share that person's claims.
- Do not work around the hooks by writing files through shell commands.

Requirements: `gh` authenticated (`gh auth login`) and `python3` on PATH.

## Working agentically

Any task bigger than a single small edit is run as three separate roles. One agent never plays two roles on the same change.

| Role | Model | Does | Does not |
|---|---|---|---|
| **Orchestrator** | Fable, main session only | Reads Issue #1, plans, splits work into tasks with non-overlapping files, posts every channel message, briefs the other roles, decides what merges | Write feature code |
| **Implementer** | Opus (medium effort for routine work, high for hard parts) | Builds one task on its own branch/worktree, writes tests, runs the checks | Post to Issue #1, touch files outside the task, merge |
| **Reviewer** | Opus, high effort | Reviews the diff against the spec with fresh eyes, runs the checks itself, tries to break it | Fix the code, see the implementer's reasoning |

Cheaper models are for support work only: Sonnet for research sweeps and smoke-test runs, Haiku for mechanical work (syncs, log parsing, report formatting). Fable is never used for subagents.

### Reviewers can send anything back

- Every change gets a reviewer verdict: `PASS` or `SEND BACK`. Nothing merges without `PASS` **and** green checks.
- A reviewer may send back for any reason it can state concretely: a bug, a failing or missing test, a contract changed without an `INTERFACE:` post, a renamed or removed contract field, a file outside the task's claim, a broken rule from `ARCHITECTURE.md` ("Rules that keep it clean"), unclear code, or work that does not match the spec.
- `SEND BACK` comes with specific findings (file, line, what is wrong, how to reproduce). The implementer fixes exactly those and resubmits to the **same** reviewer.
- After two send-backs on the same task, the orchestrator steps in: re-scope the task, split it, or post `BLOCKED:` for a human. It does not overrule the reviewer to get a merge.
- The implementer's own claim that something works is not evidence. Reviewers verify by running it.

### How the reviewer verifies, by area

| Area | Gate before `PASS` |
|---|---|
| Compiler (`api/screenplay/`) | `uv run pytest`: the sample screenplay compiles to the sample timeline, impossible actions are rejected with exact errors |
| Agent (`api/agent/`) | Tests with model calls stubbed, plus one real run on a sample story when API keys are present |
| Data (`api/db/`, `api/routes/`) | API tests, including the cross-family request that must return 404 |
| Stage (`web/components/stage/` etc.) | Type check and lint, plus screenshots of `/render/[memoryId]` at key times inspected by the reviewer. "Looks good" still needs a human. |
| Pages (`web/app/`) | Type check and lint, plus a browser run of the flow that changed |

## Dynamic workflows

Use a workflow (many agents orchestrated by a script) when a build step splits into several independent tasks. Structure every workflow like this:

1. **Sync.** Read Issue #1, fetch and rebase on `origin/main`.
2. **Plan.** The orchestrator breaks the step into tasks whose file sets do not overlap, each sized 1 to 3 hours, all inside the owner's area.
3. **Claim.** The orchestrator posts one `CLAIMING:` for the whole wave.
4. **Build in parallel.** One implementer per task, each in its own git worktree and branch.
5. **Review as each task finishes.** Do not wait for the whole wave: a task goes to its reviewer the moment its implementer is done. `SEND BACK` loops to the implementer, at most twice.
6. **Merge.** `PASS` + green checks → rebase, open a PR, merge. Merge one at a time and re-run checks after each rebase.
7. **Report.** The orchestrator posts `INTERFACE:` for any contract change as it merges, then one `DONE:` for the wave with the PR list.
8. **Re-plan.** Read Issue #1 again and plan the next wave from what merged and what teammates posted. Answer any `REQUEST` addressed to you before starting new work.

Rules for unattended (overnight) runs:

- Only work inside your own area. Contract changes are additive only and always posted.
- **Never be stagnant.** An overnight run does not idle, wait on a person, or end early. There is always a next thing to do:
  - Blocked on a human decision or a teammate's `REQUEST`? Post `BLOCKED:` and switch to another task straight away. Come back when it is answered.
  - A task was sent back twice? Park it with a note and start a different one. Do not retry the same approach a third time.
  - Waiting on checks, a merge or another agent? Start the next task in the meantime.
  - Backlog empty? Start quality passes on what is already built, in this order: (1) the `DESIGN.md` self-testing loop on every screen in your area, (2) missing tests and edge cases, (3) a bug hunt by fresh reviewers on merged code, (4) performance against the 60 FPS bar, (5) pre-rendering and checking the seeded demo family, (6) docs and the README's "Using the app" list.
  - Each quality pass must end in a concrete change or a written finding. Re-running the same check with no new result does not count as work.
- Being busy is not a reason to widen scope. Do not invent features, and do not leave your area to find work; improve what is in `README.md`, `ARCHITECTURE.md` and `DESIGN.md`.
- The only things that pause a run are a usage limit (it resumes on reset) and `main` being broken (fix or revert first).
- `main` must stay demoable. If a merge breaks checks on `main`, the next action is to revert it, before anything else.
- End with a short morning report on Issue #1: what merged, what was sent back and why, what is blocked.
