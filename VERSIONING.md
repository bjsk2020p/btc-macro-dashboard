# Versioning

## Version source

- `VERSION` (project root) is the **only** authoritative version. Its whole content is either
  `MAJOR.MINOR.PATCH` or the word `unversioned`.
- Current version: **3.0.0** (first release, 2026-11-15; snapshot in `Releases/v3.0.0/`).
- Legacy labels are **not** version sources: the filename `btc_dashboard_v2.html` and the page
  title `BTC Macro Dashboard v3` predate this scheme and disagree with each other. The active file
  keeps its path so existing links and launch habits keep working.

## When a version changes

Versions change **only when the user explicitly asks** for a new version/release or names a
number. Routine edits never bump `VERSION`, never create a snapshot, and never create a tag;
they are recorded under `## [Unreleased]` in `CHANGELOG.md`.

If a release is requested without a number, infer the increment from the Unreleased changes and
state the reason:

- **PATCH**: fixes, data-source corrections, copy or style tweaks with no new capability.
- **MINOR**: new backward-compatible capability (new indicator, table, control, data source).
- **MAJOR**: removed features, a changed entry-point path, or anything that breaks how the
  dashboard is opened or used.

If the release scope or compatibility is unclear, ask. The first release defaults to `1.0.0`
unless the user specifies another number.

## Release procedure (only on explicit request)

1. Confirm `VERSION` and `CHANGELOG.md` agree. Choose the new version and confirm that
   `Releases/v<X.Y.Z>/` does not exist yet. If it does, stop: never overwrite a release.
2. Write the version to `VERSION`.
3. In `CHANGELOG.md`, move the Unreleased items into `## [X.Y.Z] - YYYY-MM-DD` with the
   increment type, and leave an empty `## [Unreleased]` section.
4. Copy the active dashboard, unchanged, to `Releases/v<X.Y.Z>/btc_dashboard_v2.html`, with
   `serve.py`, `Open_BTC_Dashboard.cmd` and `vendor/` beside it (needed for live data and charts).
   Record the SHA-256 hashes in the changelog entry.
5. Run `node --test tests/l1_core.test.mjs tests/sources_core.test.mjs` and
   `python -m unittest tests/test_serve.py`, then launch, and confirm that both the
   active file and the snapshot load.
6. Commit or tag (`git tag v<X.Y.Z>`) **only if the user asks**. Never push.

Snapshots in `Releases/` are immutable.

## Archive structure

```
archive/
  html-dashboards/            legacy (pre-versioning) superseded dashboards
    MANIFEST.md               original path → archived path, date, version, reason
    <YYYY-MM-DD_HHMMSS>/      one batch folder per archival run; original relative paths kept
Releases/
  v<X.Y.Z>/                   immutable release snapshots (created only on explicit release)
```

Archiving rules: move only confirmed superseded dashboards (never the active entry point or its
dependencies), keep their filenames and relative paths, copy any local assets they need into the
batch, verify SHA-256 before and after, never delete, and never overwrite. Add every move to
`MANIFEST.md`. Archiving never creates a release.

## Restoring

- **An archived legacy dashboard:** find its row in `archive/html-dashboards/MANIFEST.md` and
  copy (don't move) the archived file back to its original path, or to a new name if that path
  is in use. Check its SHA-256 against the manifest. Serve over HTTP to test.
- **A released version:** copy `Releases/v<X.Y.Z>/btc_dashboard_v2.html` over the active file
  only after preserving the current one. Alternatively, once commits or tags exist, use
  `git show v<X.Y.Z>:btc_dashboard_v2.html > restored.html`.
- Restoring old code does not change `VERSION`. Record it under Unreleased, and release it only
  when asked.
