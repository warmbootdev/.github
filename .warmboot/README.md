# .warmboot/ — the warmbootdev standards in this repository

This directory is installed by `std` from
[warmbootdev/standards](https://github.com/warmbootdev/standards). Everything in it
except `std.toml` is std's and is overwritten on the next sync.

## What is whose

| Path                                                                                            | Owner           | Edit?                              |
| ----------------------------------------------------------------------------------------------- | --------------- | ---------------------------------- |
| `.warmboot/std.toml`                                                                            | this repository | yes — then `sync`                  |
| `.warmboot/std.lock`, `std.py`, `hooks/`, `modules/`, `README.md`, `prettier-packages`          | std             | no                                 |
| `.editorconfig`, `.gitignore`, `.gitattributes`, `.prettierignore`, `.github/workflows/std.yml` | std (composed)  | no — edit `<file>.local`           |
| `.prettierrc.json`                                                                              | std (merged)    | no — edit `.prettierrc.local.json` |
| `*.local`, `.prettierrc.local.json`                                                             | this repository | yes — then `sync`                  |
| `.githooks.d/<hook>/*`                                                                          | this repository | yes                                |
| `ruff.toml` / `pyproject.toml`, `Directory.Build.props`                                         | this repository | yes; keep the `extend` / `Import`  |

std writes, overwrites and deletes only paths listed in `std.lock`. It refuses to
sync over a hand-edited file of its own (`--force` discards the edit) or over a file
it does not own (`--adopt` takes it over; move anything of yours into `.local`).

## Commands

```bash
python3 .warmboot/std.py status   # version, modules, checks, this repo's overrides
python3 .warmboot/std.py check    # what the pre-push hook runs
python3 .warmboot/std.py sync     # after editing std.toml or a .local file
```

Upgrading: change `version` in `std.toml`, run `sync`, and commit the result. The
diff is everything the release changes here.

## Overrides

- **Composed text files** (`.editorconfig`, `.gitignore`, `.gitattributes`,
  `.prettierignore`): the modules' sections in order, then `<file>.local`. Later
  wins — in editorconfig a later section overrides an earlier one for the same
  files; in gitignore `!pattern` un-ignores.
- **`.prettierrc.json`**: the modules' settings merged, then `.prettierrc.local.json`
  (objects merge, lists append).
- **A check that does not apply**: `[checks] skip = ["name"]` in `std.toml`. A name
  that is not a check is reported, not ignored.
- **Module settings**: a `[<module>]` table in `std.toml` (e.g. `[dotnet] solution`).
- **`[dotnet] props-dir = "src"`**: when image builds use that directory as their context
  (so they cannot see `.warmboot/`), std also writes `src/Warmboot.Build.props`, and
  `src/Directory.Build.props` imports it: `<Import Project="$(MSBuildThisFileDirectory)Warmboot.Build.props" />`.
  Dockerfiles copy it with `Directory.Build.props`.
- **Extra hook steps**: executables in `.githooks.d/commit-msg/` or
  `.githooks.d/pre-push/`, run in name order after std's checks. These always count;
  std's own checks block only when `enforce = true`.

## Hooks

`sync` sets `core.hooksPath` to `.warmboot/hooks`. A fresh clone runs
`python3 .warmboot/std.py sync` once (it needs the standards at the pinned version,
or `--source` a checkout), or just `git config core.hooksPath .warmboot/hooks`.
