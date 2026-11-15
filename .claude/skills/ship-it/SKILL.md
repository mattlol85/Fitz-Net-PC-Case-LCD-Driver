---
name: ship-it
description: Raise a PR for the current changes in a git/GitHub repo, wait for CI, and only if it passes squash-merge, cut a release tag, and deploy (where the repo has a deploy). Args - optional repo path, bump type (patch|minor|major), and commit/PR title. Use when asked to ship, release, or "PR, merge, tag and deploy" a change.
---

# ship-it

End-to-end: **branch -> PR -> CI green -> squash-merge -> release tag -> deploy -> verify.**
Stop and report at the first failure; never merge on red or pending CI.

Every repo-specific fact (default branch, check command, release and deploy
mechanism, health endpoints) is **discovered**, not assumed. If a repo's
`CLAUDE.md` or a project-level `ship-it` skill states one of these, that wins
over anything inferred below.

## 0. Inputs and discovery

- **Repo**: the repo holding the changes (default: the current working tree, or
  infer from the files the user just worked on; confirm if ambiguous). Call its
  root `$REPO_DIR` and its GitHub slug `$SLUG`:
  ```bash
  cd "$REPO_DIR"
  gh repo view --json nameWithOwner,defaultBranchRef -q '.nameWithOwner + " " + .defaultBranchRef.name'
  ```
- **Default branch** `$BASE`: from the `gh` call above; fallback
  `git symbolic-ref --short refs/remotes/origin/HEAD | sed 's#^origin/##'`
  (run `git remote set-head origin -a` first if that ref is missing). Never
  assume `main` or `master`.
- **Bump**: `patch` for fixes/UI polish, `minor` for features, `major` only if
  the user says so. Default: infer from the commit type (`fix`/`chore` -> patch,
  `feat` -> minor).
- **Title**: conventional commit, `type(subject): description`. Match the repo's
  existing style if `git log --oneline -20` shows a different convention.
- **Read the repo's guidance** before acting: `CLAUDE.md` (root and nested),
  `AGENTS.md`, `CONTRIBUTING.md`, README "Development"/"Release" sections, and
  `.github/workflows/*`. Note any rules about who owns version bumps, required
  checks, or deploy procedure.

## 1. Branch and commit (only the intended changes)

```bash
git fetch origin && git status --short && git branch --show-current
```

- Stage **only files that belong to this change** by name. Leave unrelated
  modified/untracked files alone and mention them in the summary. If you can't
  tell which files belong to the change, ask.
- If the current branch is `$BASE`, or is a different feature branch with its
  own unmerged commits, create a fresh branch from `origin/$BASE`
  (`git switch -c <type>/<slug> origin/$BASE` carries the working-tree changes
  across). Never commit to `$BASE` directly.
- Don't hand-edit version numbers if a release workflow owns them (see step 6).
- Commit with the conventional message and the attribution trailer from the
  session's system reminder.

## 2. Local checks before pushing

Discover the repo's own check command, in this order, and use the first that
clearly applies:

1. A command named in `CLAUDE.md` / `CONTRIBUTING.md` / README (e.g. a
   one-shot check script, `make check`).
2. Task runners: `package.json` `scripts` (`test`, `lint`, `typecheck`, `check`),
   `Makefile` / `justfile` / `Taskfile.yml` targets, `tox.ini` / `noxfile.py`.
3. Ecosystem defaults: `pyproject.toml` tool config (pytest, ruff, black,
   mypy), `./gradlew test`, `mvn verify`, `cargo test` + `cargo clippy`,
   `go test ./...`, `dotnet test`.
4. Whatever the CI workflow on PRs actually runs (`.github/workflows/*.y*ml`) -
   mirror its lint/test steps locally, with the **same scope** (if CI lints the
   whole repo, lint the whole repo, not just changed files).

Use the repo's virtualenv / toolchain wrapper if it has one. If checks fail,
fix or stop - don't push red. If you genuinely can't find any check command,
say so and rely on CI.

## 3. Raise the PR

```bash
git push -u origin HEAD
gh pr create -R "$SLUG" --base "$BASE" --title "<title>" --body "<summary + test plan + PR attribution line>"
```

Use the repo's PR template (`.github/pull_request_template.md`) if present.

## 4. Gate on CI

```bash
gh pr checks <PR#> -R "$SLUG" --watch --fail-fast
```

- **All pass** -> continue.
- **Any fail / cancelled / no checks reported** -> STOP. Show the failing
  job's log (`gh run view <run-id> -R "$SLUG" --log-failed`) and ask the user.
  Do not merge. (If the repo has no CI at all, say so and ask before merging.)

## 5. Merge

```bash
gh pr merge <PR#> -R "$SLUG" --squash --delete-branch
git switch "$BASE" && git pull
```

Use squash unless the repo disallows it (`gh repo view --json squashMergeAllowed`)
or its guidance says otherwise. If merging is blocked (required reviews,
conflicts, merge queue), stop and report.

## 6. Tag / release

Find the repo's release mechanism:

```bash
ls .github/workflows
grep -lE 'workflow_dispatch|release:|tags:' .github/workflows/* 2>/dev/null
```

- **A release workflow exists** (typically `workflow_dispatch` with a bump input
  that bumps the version, tags `vX.Y.Z`, and creates the GitHub Release): read
  it to get its file name and input name/choices, then dispatch it against
  `$BASE` and watch it.
  ```bash
  gh workflow run <release-workflow> -R "$SLUG" --ref "$BASE" -f <bump-input>=<patch|minor|major>
  RID=$(gh run list -R "$SLUG" -w <release-workflow> -L1 --json databaseId -q '.[0].databaseId')
  gh run watch -R "$SLUG" "$RID" --exit-status
  git pull --tags && git tag --sort=-v:refname | head -1   # new tag = $TAG
  ```
  Make sure the run you watch is the one you just dispatched (check its
  created-at time), not an older one.
- **A tag-triggered workflow exists** (`on: push: tags:`) but no dispatchable
  one: create and push the tag manually (below); that triggers the release.
- **No release automation**: compute the next semver from
  `git tag --sort=-v:refname | head -1`, matching the existing tag format
  (`v` prefix or not), then
  `git tag -a $TAG -m "Release $TAG" && git push origin $TAG`
  and `gh release create $TAG --generate-notes -R "$SLUG"`.
  Confirm the new tag with the user first if the repo has no prior tags.
- If the repo bumps a version file by hand (no automation), make that bump part
  of the PR in step 1, not a direct commit to `$BASE`.

## 7. Deploy

Only if the repo actually deploys somewhere. Look for a deploy workflow
(`grep -liE 'deploy' .github/workflows/*`), a deploy skill, or deploy notes in
`CLAUDE.md`/README. If there is none (libraries, desktop apps, the release
itself is the deliverable), skip to step 8 and say so.

- Releases created with `GITHUB_TOKEN` do **not** trigger other workflows'
  `release: published` events, so a release-triggered deploy may not fire.
  First check whether a deploy run already started
  (`gh run list -R "$SLUG" -w <deploy-workflow> -L3`); if not, dispatch it
  explicitly with the input its `workflow_dispatch` declares (often `tag` or
  `version`) set to `$TAG`. Don't double-dispatch.
- Don't run deploys for services that share infrastructure concurrently unless
  the repo says that's safe; deploy one at a time.
- Watch with `gh run watch <id> -R "$SLUG" --exit-status`. If it sits queued
  for more than ~30s on a self-hosted runner, the runner is probably down -
  report it.

## 8. Verify and report

- If the repo documents a health/info endpoint or a smoke check, hit it
  (`curl -fsS <url>`) and confirm the live version matches `$TAG` where it
  exposes one.
- Otherwise verify the release itself: `gh release view $TAG -R "$SLUG"`
  (expected assets attached, not a draft).

Final report: PR link, merged commit, tag, release/deploy run links, live or
release check result, and any files deliberately left out of the commit. On a
bad deploy, roll back by re-dispatching the deploy with the previous tag.

## Guardrails

- Merge, tag, and deploy are outward-facing: only proceed when the user invoked
  this skill (that is the authorization), and only for the repo it was invoked
  on. Don't extend it to other repos.
- Never `--no-verify`, never force-push, never merge with failing/pending checks.
- Never commit, stage, stash, or reformat files that aren't part of this change.

## This repo (FitzLCD)

Repo facts for `mattlol85/Fitz-Net-PC-Case-LCD-Driver`; these override the
discovery steps above. Still confirm `$BASE` with `gh` (currently `master`).

- **Step 2 check**: one process runs black check + ruff + pytest:
  `.\.venv\Scripts\python.exe tools/check.py` (`--fix` to reformat/autofix
  first). CI (`.github/workflows/python-build.yaml`) runs `ruff check .`
  **repo-wide**, so a pre-existing violation in any file fails the PR. Run it
  across the whole repo, not just on the files you touched.
- **Step 1 versions**: don't edit the version in `pyproject.toml` or
  `src/fitzlcd/__init__.py` by hand. `publish.yml` owns both.
- **Step 6 release**: `publish.yml` (`workflow_dispatch`, input
  `increment-type` = `major|minor|patch`) bumps the version, tags `vX.Y.Z`,
  builds the PyInstaller EXE, and attaches `FitzLCD-<ver>-windows.zip` and
  `FitzLCD-<ver>-Setup.exe` to the GitHub Release:
  ```bash
  gh workflow run publish.yml -R mattlol85/Fitz-Net-PC-Case-LCD-Driver --ref master -f increment-type=<patch|minor|major>
  ```
  Its job order (version -> build -> release) is load-bearing; don't work
  around it with a manual tag.
- **Step 7 deploy**: none. It's a desktop app, and installs pick up the
  release through the in-app self-updater.
- **Step 8 verify**: `gh release view $TAG` shows both assets. The updater only
  looks for the `-windows.zip` (`updater._ASSET_RE`), so a missing zip means
  users get no update.
