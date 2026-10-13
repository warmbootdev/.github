# Contributing

Thanks for helping. These apply to every warmbootdev repository unless it has its
own CONTRIBUTING.md.

## Before you start

Run the repository's setup once after cloning, which turns on the shared git
hooks:

```bash
python3 .warmboot/std.py sync    # or: git config core.hooksPath .warmboot/hooks
```

`python3 .warmboot/std.py check` runs what the pre-push hook and CI run.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/):
`<type>(<scope>)?: <summary>`, where type is one of `feat`, `fix`, `docs`, `style`,
`refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`. Summary in the
imperative, no trailing period, the whole subject at most 100 characters. A
breaking change adds `!` after the type or scope.

Pull requests are squash-merged, so the PR title follows the same rule.

## Pull requests

- One change per pull request; say why in the description, not only what.
- Include the tests that prove it, and note anything you could not verify.
- Formatting and lint come from the shared standards; don't hand-tune them in a
  feature change. If a rule is wrong, change it in the standards.
