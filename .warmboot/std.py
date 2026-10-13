#!/usr/bin/env python3
"""std: the warmbootdev standards, installed and kept current in a repository.

    std init   --modules base,python [--source DIR] [--version vX.Y.Z]
    std sync   [--source DIR] [--adopt] [--force]
    std check  [--ci] [--hook NAME] [--only NAME ...]
    std status
    std hook   NAME [ARGS...]          (what .warmboot/hooks/* call)

In a consuming repository:

    .warmboot/std.toml     the manifest. Yours: version, modules, overrides.
    .warmboot/std.lock     every file std wrote, with its hash. std's.
    .warmboot/std.py, hooks/, modules/<m>/
                           vendored from warmbootdev/standards. std's.
    .editorconfig, .gitignore, .gitattributes, .prettierignore, .prettierrc.json,
    .github/workflows/std.yml
                           composed from the modules' fragments, then <file>.local
                           (yours) appended or merged last. std's: edit the .local.
    .githooks.d/<hook>/*   your own hook steps, run after std's. Yours.

std only ever writes, overwrites or deletes paths recorded in std.lock. Anything
else in the repository is somebody's and is left alone.

Python 3.11+, standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DIR = ".warmboot"
MANIFEST = f"{DIR}/std.toml"
LOCK = f"{DIR}/std.lock"
UPSTREAM = os.environ.get("STD_UPSTREAM", "git@github.com:warmbootdev/standards.git")
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "warmboot-std"

# Composed root files: fragment name in a module -> (path, how it combines).
# "text": fragments concatenated in module order, then <path>.local.
# "json": fragments deep-merged in module order (lists concatenated, de-duplicated),
#         then <stem>.local.json merged last.
# "lines": like text, without comments; a plain list a check reads.
COMPOSED = {
    "editorconfig": (".editorconfig", "text"),
    "gitignore": (".gitignore", "text"),
    "gitattributes": (".gitattributes", "text"),
    "prettierignore": (".prettierignore", "text"),
    "prettierrc.json": (".prettierrc.json", "json"),
    "prettier-packages": (f"{DIR}/prettier-packages", "lines"),
}
WORKFLOW = ".github/workflows/std.yml"
ACTIONLINT = ".github/actionlint.yaml"
TEMPLATE = f"{DIR}/workflow/std.yml"  # vendored, so `check` can re-render offline
HOSTED = re.compile(r"^(ubuntu|windows|macos)-")
HOOKS = ("commit-msg", "pre-push")


def die(msg: str) -> None:
    print(f"std: {msg}", file=sys.stderr)
    sys.exit(2)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        die(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def repo_root() -> Path:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if r.returncode != 0:
        die("not inside a git repository")
    return Path(r.stdout.strip())


# ---------------------------------------------------------------- manifest ----


@dataclass
class Manifest:
    version: str
    modules: list[str]
    enforce: bool = False
    skip: list[str] = field(default_factory=list)
    runs_on: str = "ubuntu-latest"
    workflow: bool = True
    labels: list[str] = field(default_factory=list)  # other self-hosted labels in use
    settings: dict[str, dict] = field(default_factory=dict)  # [<module>] tables

    @classmethod
    def load(cls, root: Path) -> Manifest:
        path = root / MANIFEST
        if not path.exists():
            die(f"{MANIFEST} not found; start with `std init`")
        data = tomllib.loads(path.read_text())
        known = {"version", "modules", "enforce", "checks", "ci"}
        return cls(
            version=data.get("version", ""),
            modules=list(data.get("modules", [])),
            enforce=bool(data.get("enforce", False)),
            skip=list(data.get("checks", {}).get("skip", [])),
            runs_on=data.get("ci", {}).get("runs-on", "ubuntu-latest"),
            workflow=bool(data.get("ci", {}).get("workflow", True)),
            labels=list(data.get("ci", {}).get("labels", [])),
            settings={k: v for k, v in data.items() if k not in known and isinstance(v, dict)},
        )


def write_manifest(root: Path, version: str, modules: list[str]) -> None:
    mods = ", ".join(f'"{m}"' for m in modules)
    (root / DIR).mkdir(exist_ok=True)
    (root / MANIFEST).write_text(
        f"""# The warmbootdev standards this repository follows. Yours to edit; run
# `python3 .warmboot/std.py sync` afterwards. See .warmboot/README.md.

version = "{version}"
modules = [{mods}]

# false: checks report problems but never block a commit, push or CI run.
enforce = false

[checks]
# Check names this repository does not run (`std status` lists them).
skip = []

[ci]
runs-on = "ubuntu-latest"
# false: std does not write .github/workflows/std.yml (run `std check --ci` yourself).
workflow = true
"""
    )


# ----------------------------------------------------------------- modules ----


@dataclass
class Check:
    module: str
    name: str
    script: str  # relative to the module directory
    when: list[str]
    always: bool = False  # blocks even with enforce = false, and cannot be skipped


@dataclass
class Module:
    name: str
    path: Path  # in the source, or vendored under .warmboot/modules
    description: str
    requires: list[str]
    checks: list[Check]

    @classmethod
    def load(cls, path: Path) -> Module:
        meta = tomllib.loads((path / "module.toml").read_text())
        checks = [
            Check(
                path.name,
                name,
                spec["script"],
                list(spec.get("when", ["pre-push", "ci"])),
                bool(spec.get("always", False)),
            )
            for name, spec in meta.get("checks", {}).items()
        ]
        return cls(path.name, path, meta.get("description", ""), meta.get("requires", []), checks)

    def fragment(self, name: str) -> str | None:
        f = self.path / "fragments" / name
        return f.read_text() if f.exists() else None


def resolve(modules_dir: Path, wanted: list[str]) -> list[Module]:
    """The wanted modules plus what they require, base first, in a stable order."""
    order: list[Module] = []
    seen: set[str] = set()

    def visit(name: str, chain: tuple[str, ...]) -> None:
        if name in seen:
            return
        if name in chain:
            die(f"module requirement cycle: {' -> '.join((*chain, name))}")
        path = modules_dir / name
        if not (path / "module.toml").exists():
            available = sorted(
                p.name for p in modules_dir.iterdir() if (p / "module.toml").exists()
            )
            die(f"unknown module {name!r}; available: {', '.join(available)}")
        mod = Module.load(path)
        for req in mod.requires:
            visit(req, (*chain, name))
        seen.add(name)
        order.append(mod)

    for name in ["base", *wanted]:
        visit(name, ())
    return order


# --------------------------------------------------------------- composing ----


def header(path: str, comment: str = "#") -> str:
    local = f"{path}.local"
    return (
        f"{comment} Generated by std (warmbootdev/standards; version in std.lock). Do not edit:\n"
        f"{comment} put this repository's additions in {local} and run\n"
        f"{comment} `python3 .warmboot/std.py sync`. See .warmboot/README.md.\n"
    )


def merge(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge(out[k], v) if k in out else v
        return out
    if isinstance(a, list) and isinstance(b, list):
        return a + [x for x in b if x not in a]
    return b


def local_path(path: str, kind: str) -> str:
    if kind == "json":
        stem, _, ext = path.rpartition(".")
        return f"{stem}.local.{ext}"
    return f"{path}.local"


def compose(
    root: Path, modules: list[Module], version: str, owned: list[str] | None = None
) -> dict[str, bytes]:
    """Every composed file's exact bytes, from module fragments and the repo's .local files."""
    out: dict[str, bytes] = {}
    for frag, (path, kind) in COMPOSED.items():
        parts = [(m.name, m.fragment(frag)) for m in modules]
        parts = [(n, t) for n, t in parts if t is not None]
        if not parts:
            continue
        local = root / local_path(path, kind)
        if kind == "json":
            data: object = {}
            for _, text in parts:
                data = merge(data, json.loads(text))
            if local.exists():
                data = merge(data, json.loads(local.read_text()))
            out[path] = (json.dumps(data, indent=2) + "\n").encode()
            continue
        if kind == "lines":
            lines = [ln.strip() for _, t in parts for ln in t.splitlines()]
            if local.exists():
                lines += [ln.strip() for ln in local.read_text().splitlines()]
            lines = [ln for ln in lines if ln and not ln.startswith("#")]
            out[path] = ("\n".join(dict.fromkeys(lines)) + "\n").encode()
            continue
        chunks = [header(path)]
        for name, text in parts:
            chunks.append(f"\n# ---- {name} ----\n{text.rstrip()}\n")
        if local.exists():
            chunks.append(f"\n# ---- {local.name} ----\n{local.read_text().rstrip()}\n")
        if path == ".gitignore" and owned:
            # Last, so no directory pattern above (a module's bin/, say) keeps std's
            # own files out of git: re-include exactly the directories they are in.
            dirs = sorted({str(p) for f in owned for p in Path(f).parents if str(p) != "."})
            lines = "".join(f"!/{d}/\n" for d in dirs)
            chunks.append(f"\n# ---- std: its own files are always tracked ----\n{lines}")
        out[path] = "".join(chunks).encode()
    return out


def ci_files(template: str, manifest: Manifest) -> dict[str, bytes]:
    """The workflow, and the actionlint config that declares self-hosted runner
    labels (actionlint rejects any label it does not know)."""
    out: dict[str, bytes] = {}
    if manifest.workflow:
        out[WORKFLOW] = template.replace("{{runs-on}}", manifest.runs_on).encode()
    labels = [x for x in dict.fromkeys([manifest.runs_on, *manifest.labels]) if not HOSTED.match(x)]
    if labels:
        items = "".join(f"    - {x}\n" for x in labels)
        out[ACTIONLINT] = (
            "# Generated by std (warmbootdev/standards). Do not edit: set [ci] runs-on\n"
            "# and labels in .warmboot/std.toml, then `python3 .warmboot/std.py sync`.\n"
            f"self-hosted-runner:\n  labels:\n{items}"
        ).encode()
    return out


# ------------------------------------------------------------------ source ----


def fetch_source(version: str, source: str | None) -> tuple[Path, str]:
    """The standards checkout to install from, and the commit it is at."""
    if source:
        path = Path(source).resolve()
        if not (path / "std.py").exists() or not (path / "modules").is_dir():
            die(f"{path} is not a warmbootdev/standards checkout")
        commit = git(path, "rev-parse", "HEAD", check=False) or "unknown"
        dirty = git(path, "status", "--porcelain", check=False)
        return path, commit + ("-dirty" if dirty else "")
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version):
        die(f"version {version!r} is not a release tag; use --source for a working copy")
    path = CACHE / version
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=CACHE))
        r = subprocess.run(
            ["git", "clone", "-q", "--depth", "1", "--branch", version, UPSTREAM, str(tmp)],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            die(f"cannot fetch {UPSTREAM} {version}: {r.stderr.strip()}")
        tmp.rename(path)
    return path, git(path, "rev-parse", "HEAD")


# ------------------------------------------------------------------- lock ----


def read_lock(root: Path) -> dict:
    p = root / LOCK
    return json.loads(p.read_text()) if p.exists() else {"files": {}}


def module_files(source: Path, module: Path) -> list[Path]:
    """A module's files as the standards repository has them: tracked by git, so a
    working copy's caches and scratch (a .ruff_cache, an editor backup) are never
    vendored. A source that is not a git checkout contributes every file."""
    r = subprocess.run(
        ["git", "-C", str(source), "ls-files", "-z", "--", str(module.relative_to(source))],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return sorted(f for f in module.rglob("*") if f.is_file())
    return sorted(source / p for p in r.stdout.split("\0") if p)


def planned_files(root: Path, source: Path, manifest: Manifest) -> dict[str, bytes]:
    """Every path std owns in this repository, with its exact contents."""
    modules = resolve(source / "modules", manifest.modules)
    files: dict[str, bytes] = {f"{DIR}/std.py": (source / "std.py").read_bytes()}
    for hook in HOOKS:
        files[f"{DIR}/hooks/{hook}"] = (source / "hooks" / hook).read_bytes()
    files[f"{DIR}/README.md"] = (source / "consumer-README.md").read_bytes()
    for m in modules:
        for f in module_files(source, m.path):
            files[f"{DIR}/modules/{m.name}/{f.relative_to(m.path)}"] = f.read_bytes()
    template = (source / "workflow" / "std.yml").read_bytes()
    files[TEMPLATE] = template
    vendored = [p for p in files if p.startswith(f"{DIR}/")]
    files.update(compose(root, modules, manifest.version, vendored))
    files.update(ci_files(template.decode(), manifest))
    return files


# ------------------------------------------------------------------- sync ----


def cmd_init(args) -> None:
    root = repo_root()
    if (root / MANIFEST).exists():
        die(f"{MANIFEST} already exists; edit it and run `std sync`")
    modules = [m for m in args.modules.split(",") if m and m != "base"]
    write_manifest(root, args.version, modules)
    print(f"wrote {MANIFEST}")
    cmd_sync(args)


def cmd_sync(args) -> None:
    root = repo_root()
    manifest = Manifest.load(root)
    source, commit = fetch_source(manifest.version, args.source)

    # Always install with the requested version's own std.py: it knows its layout.
    here = Path(__file__).resolve()
    theirs = (source / "std.py").resolve()
    if here != theirs and os.environ.get("STD_REEXEC") != "1":
        env = dict(os.environ, STD_REEXEC="1")
        argv = [sys.executable, str(theirs), *sys.argv[1:]]
        if not args.source:
            argv += ["--source", str(source)]
        sys.exit(subprocess.run(argv, env=env, cwd=root).returncode)

    lock = read_lock(root)
    owned: dict[str, str] = lock.get("files", {})
    plan = planned_files(root, source, manifest)

    # Refuse to destroy anybody's work: hand edits to owned files, and files std
    # would write that it does not own.
    problems = []
    for path, digest in owned.items():
        p = root / path
        if p.exists() and sha256(p.read_bytes()) != digest and not args.force:
            problems.append(
                f"{path} was edited by hand (put it in the .local file; --force discards)"
            )
    for path in plan:
        p = root / path
        if path not in owned and p.exists() and p.read_bytes() != plan[path] and not args.adopt:
            problems.append(f"{path} exists and std does not own it (--adopt takes it over)")
    if problems:
        die("refusing to sync:\n  " + "\n  ".join(problems))

    adopted = [p for p in plan if p not in owned and (root / p).exists()]
    for path in sorted(set(owned) - set(plan)):
        (root / path).unlink(missing_ok=True)
        prune_empty_dirs(root, (root / path).parent)
    for path, data in plan.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists() or p.read_bytes() != data:
            p.write_bytes(data)
        if path.startswith(f"{DIR}/hooks/") or path.endswith(".sh") or path == f"{DIR}/std.py":
            p.chmod(0o755)
    new_lock = {
        "version": manifest.version,
        "source": commit,
        "files": {path: sha256(data) for path, data in sorted(plan.items())},
    }
    (root / LOCK).write_text(json.dumps(new_lock, indent=2) + "\n")
    install_hooks(root)

    removed = sorted(set(owned) - set(plan))
    changed = sorted(p for p in plan if owned.get(p) != new_lock["files"][p])
    print(f"std {manifest.version} ({commit[:12]}): {len(plan)} files, {len(changed)} changed")
    locals_by_path = {path: local_path(path, kind) for path, kind in COMPOSED.values()}
    for p in adopted:
        where = locals_by_path.get(p)
        hint = (
            f"anything it had of yours belongs in {where} now"
            if where
            else "it is std's from now on"
        )
        print(f"  adopted {p}: {hint}")
    for p in removed:
        print(f"  removed {p}")


def prune_empty_dirs(root: Path, d: Path) -> None:
    while d != root and d.is_dir() and not any(d.iterdir()):
        d.rmdir()
        d = d.parent


def install_hooks(root: Path) -> None:
    current = git(root, "config", "--get", "core.hooksPath", check=False)
    if current != f"{DIR}/hooks":
        git(root, "config", "core.hooksPath", f"{DIR}/hooks")
        if current:
            print(f"  core.hooksPath: {current} -> {DIR}/hooks (its hooks belong in .githooks.d/)")


# ------------------------------------------------------------------ check ----


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    always: bool = False


def integrity(root: Path, manifest: Manifest) -> list[Result]:
    """Is what is committed exactly what std would write? Works offline: the
    vendored copies are themselves hashed in the lock."""
    out: list[Result] = []
    lock = read_lock(root)
    owned = lock.get("files", {})
    if not owned:
        return [Result("std installed", False, f"no {LOCK}; run `std sync`")]
    bad = []
    for path, digest in owned.items():
        p = root / path
        if not p.exists():
            bad.append(f"{path} is missing")
        elif sha256(p.read_bytes()) != digest:
            bad.append(f"{path} was edited by hand; edit its .local file and run `std sync`")
    out.append(Result("std files unmodified", not bad, "\n".join(bad)))

    stale = []
    if lock.get("version") != manifest.version:
        stale.append(f"{MANIFEST} asks for {manifest.version}, installed is {lock.get('version')}")
    vendored = root / DIR / "modules"
    installed = {p.name for p in vendored.iterdir()} if vendored.is_dir() else set()
    missing = sorted({"base", *manifest.modules} - installed)
    if missing:
        stale.append(f"{MANIFEST} lists modules that are not installed: {', '.join(missing)}")
    if not stale:
        modules = resolve(vendored, manifest.modules)
        vendored = [p for p in owned if p.startswith(f"{DIR}/")]
        for path, data in compose(root, modules, manifest.version, vendored).items():
            p = root / path
            if not p.exists() or p.read_bytes() != data:
                stale.append(f"{path} is out of date with its .local file")
        template = root / TEMPLATE
        expected = ci_files(template.read_text(), manifest) if template.exists() else {}
        for path in (WORKFLOW, ACTIONLINT):
            p = root / path
            if path in expected and (not p.exists() or p.read_bytes() != expected[path]):
                stale.append(f"{path} is out of date with [ci] in {MANIFEST}")
            elif path not in expected and path in owned:
                stale.append(f"{path} is no longer wanted by [ci] in {MANIFEST}")
    out.append(
        Result(
            "std files current", not stale, "\n".join(stale + ["run `std sync`"] if stale else [])
        )
    )
    return out


def installed(root: Path, manifest: Manifest) -> list[Module] | None:
    """The manifest's modules as vendored here; None when some are missing (the
    integrity report says which, and nothing else can be checked meaningfully)."""
    vendored = root / DIR / "modules"
    have = {p.name for p in vendored.iterdir()} if vendored.is_dir() else set()
    if not {"base", *manifest.modules} <= have:
        return None
    return resolve(vendored, manifest.modules)


def joins(root: Path, manifest: Manifest) -> list[Result]:
    """Things the standards name that have to resolve in this repository."""
    out: list[Result] = []
    modules = installed(root, manifest)
    if modules is None:
        return out
    names = {c.name for m in modules for c in m.checks}
    unknown = [s for s in manifest.skip if s not in names]
    out.append(
        Result(
            "skip list names real checks",
            not unknown,
            f"[checks] skip names {', '.join(unknown)}, which no module has (checks: {', '.join(sorted(names))})"
            if unknown
            else "",
        )
    )
    mod_names = {m.name for m in modules}
    if "python" in mod_names:
        base = f"{DIR}/modules/python/ruff.toml"
        configs = [root / n for n in ("ruff.toml", ".ruff.toml", "pyproject.toml")]
        extends = any(c.exists() and base in c.read_text() for c in configs)
        out.append(
            Result(
                "ruff config extends the standard",
                extends,
                f'add extend = "{base}" to ruff.toml (or [tool.ruff] in pyproject.toml)',
            )
        )
    if "dotnet" in mod_names:
        props = root / "Directory.Build.props"
        target = f"{DIR}/modules/dotnet/Warmboot.Build.props"
        ok = props.exists() and target in props.read_text()
        out.append(
            Result(
                "Directory.Build.props imports the standard",
                ok,
                f'add <Import Project="$(MSBuildThisFileDirectory){target}" /> to Directory.Build.props',
            )
        )
    owned = list(read_lock(root).get("files", {}))
    r = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--no-index", "--stdin"],
        input="\n".join(owned),
        capture_output=True,
        text=True,
    )
    ignored = r.stdout.split()
    out.append(
        Result(
            "std files not gitignored",
            not ignored,
            "git ignores these, so they would never be committed: " + ", ".join(ignored),
        )
    )
    for hook in HOOKS:
        d = root / ".githooks.d" / hook
        if d.is_dir():
            not_exec = [p.name for p in d.iterdir() if p.is_file() and not os.access(p, os.X_OK)]
            if not_exec:
                out.append(
                    Result(f".githooks.d/{hook} steps executable", False, ", ".join(not_exec))
                )
    return out


def commit_range(hook_stdin: str | None) -> str:
    """The commits being checked: `git log` revision arguments, one set per pushed
    ref, separated by ';'. Empty when there is nothing to check."""
    if hook_stdin is not None:  # pre-push: "<lref> <lsha> <rref> <rsha>" per line
        ranges = []
        for line in hook_stdin.splitlines():
            parts = line.split()
            if len(parts) != 4 or set(parts[1]) == {"0"}:
                continue
            ranges.append(
                f"{parts[1]} --not --remotes"
                if set(parts[3]) == {"0"}
                else f"{parts[3]}..{parts[1]}"
            )
        return ";".join(ranges)
    base, head = os.environ.get("STD_BASE", ""), os.environ.get("STD_HEAD", "")
    if not head:
        return ""
    if not base or set(base) == {"0"}:
        return f"{head}^!"
    r = subprocess.run(["git", "cat-file", "-e", f"{base}^{{commit}}"], capture_output=True)
    return f"{base}..{head}" if r.returncode == 0 else f"{head}^!"


def run_checks(
    root: Path, manifest: Manifest, when: str, rng: str, only: list[str]
) -> list[Result]:
    out: list[Result] = []
    vendored = root / DIR / "modules"
    modules = installed(root, manifest)
    if modules is None:
        return out
    env = dict(os.environ, STD_ROOT=str(root), STD_RANGE=rng, STD_WHEN=when)
    for mod, settings in manifest.settings.items():
        for k, v in settings.items():
            env[f"STD_{mod.upper()}_{k.upper().replace('-', '_')}"] = str(v)
    for m in modules:
        for c in m.checks:
            skipped = c.name in manifest.skip and not c.always
            if skipped or when not in c.when or (only and c.name not in only):
                continue
            print(f"::group::{c.name}" if when == "ci" else f"--- {c.name}", flush=True)
            r = subprocess.run(["bash", str(vendored / m.name / c.script)], cwd=root, env=env)
            if when == "ci":
                print("::endgroup::", flush=True)
            detail = "" if r.returncode == 0 else f"exit {r.returncode}"
            out.append(Result(c.name, r.returncode == 0, detail, c.always))
    return out


def report(results: list[Result], enforce: bool, ci: bool) -> int:
    failed = [r for r in results if not r.ok]
    for r in results:
        print(f"{'ok' if r.ok else 'FAIL':>4}  {r.name}")
        if not r.ok and r.detail:
            for line in r.detail.splitlines():
                print(f"      {line}")
    if not failed:
        return 0
    blocking = [r for r in failed if enforce or r.always]
    if ci:
        for r in failed:
            level = "error" if r in blocking else "warning"
            msg = (r.detail or "failed").replace("\n", "%0A")
            print(f"::{level} title=std: {r.name}::{msg}")
    if blocking:
        names = ", ".join(r.name for r in blocking)
        print(f"std: {len(failed)} check(s) failed; blocking: {names}", file=sys.stderr)
        return 1
    print(
        f"std: {len(failed)} check(s) failed -- reported only (enforce = false in {MANIFEST})",
        file=sys.stderr,
    )
    return 0


def cmd_check(args) -> None:
    root = repo_root()
    manifest = Manifest.load(root)
    when = "ci" if args.ci else (args.hook or "pre-push")
    results = integrity(root, manifest) + joins(root, manifest)
    rng = commit_range(args.stdin)
    if args.ci and os.environ.get("STD_PR_TITLE"):
        os.environ["STD_SUBJECTS_EXTRA"] = os.environ["STD_PR_TITLE"]
    results += run_checks(root, manifest, when, rng, args.only or [])
    sys.exit(report(results, manifest.enforce, args.ci))


# ------------------------------------------------------------------- hook ----


def cmd_hook(args) -> None:
    """A git hook: std's part (reported or enforced per the manifest), then the
    repository's own .githooks.d/<hook>/* steps, which always count."""
    root = repo_root()
    stdin = sys.stdin.read() if args.name == "pre-push" else None
    status = 0
    if (root / MANIFEST).exists():
        manifest = Manifest.load(root)
        if args.name == "commit-msg":
            # AI attribution is refused outright, whatever enforce says.
            guard = root / DIR / "modules/base/bin/check-attribution.sh"
            if subprocess.run(["bash", str(guard), "--file", args.args[0]], cwd=root).returncode:
                status = 1
            script = root / DIR / "modules/base/bin/check-commits.sh"
            r = subprocess.run(["bash", str(script), "--file", args.args[0]], cwd=root)
            if r.returncode != 0:
                if manifest.enforce:
                    status = 1
                else:
                    print(f"std: reported only (enforce = false in {MANIFEST})", file=sys.stderr)
        elif args.name == "pre-push":
            results = integrity(root, manifest) + joins(root, manifest)
            results += run_checks(root, manifest, "pre-push", commit_range(stdin), [])
            status = report(results, manifest.enforce, ci=False)
    local = root / ".githooks.d" / args.name
    if local.is_dir():
        for step in sorted(p for p in local.iterdir() if p.is_file()):
            r = subprocess.run([str(step), *args.args], cwd=root, input=stdin, text=True)
            if r.returncode != 0:
                print(
                    f"std: .githooks.d/{args.name}/{step.name} failed (exit {r.returncode})",
                    file=sys.stderr,
                )
                status = 1
    sys.exit(status)


# ----------------------------------------------------------------- status ----


def cmd_status(_args) -> None:
    root = repo_root()
    manifest = Manifest.load(root)
    lock = read_lock(root)
    vendored = root / DIR / "modules"
    print(
        f"version   {manifest.version} (installed {lock.get('version', 'none')}, {lock.get('source', '?')[:12]})"
    )
    print(f"enforce   {manifest.enforce}")
    if vendored.is_dir():
        mods = resolve(vendored, manifest.modules)
        print(f"modules   {', '.join(m.name for m in mods)}")
        for m in mods:
            for c in m.checks:
                state = "skipped" if c.name in manifest.skip else ", ".join(c.when)
                print(f"  check   {c.name:<22} {m.name:<8} {state}")
    for _, (path, kind) in COMPOSED.items():
        lp = local_path(path, kind)
        if (root / lp).exists():
            print(f"override  {lp}")
    for hook in HOOKS:
        d = root / ".githooks.d" / hook
        if d.is_dir():
            for s in sorted(d.iterdir()):
                print(f"hook      .githooks.d/{hook}/{s.name}")
    for mod, settings in manifest.settings.items():
        for k, v in settings.items():
            print(f"setting   {mod}.{k} = {v}")


def main() -> None:
    p = argparse.ArgumentParser(prog="std", description=(__doc__ or "").split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init", help="write .warmboot/std.toml and install")
    s.add_argument("--modules", default="")
    s.add_argument("--version", default="v0.1.9")
    s.add_argument("--source", help="a standards checkout instead of the release tag")
    s.add_argument("--adopt", action="store_true", help="take over existing root config files")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("sync", help="install or update to the manifest's version")
    s.add_argument("--source")
    s.add_argument("--adopt", action="store_true")
    s.add_argument("--force", action="store_true", help="discard hand edits to std's files")
    s.set_defaults(fn=cmd_sync)
    s = sub.add_parser("check", help="integrity, joins and the modules' checks")
    s.add_argument("--ci", action="store_true")
    s.add_argument("--hook")
    s.add_argument("--only", action="append")
    s.set_defaults(fn=cmd_check, stdin=None)
    s = sub.add_parser("status")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("hook")
    s.add_argument("name", choices=HOOKS)
    s.add_argument("args", nargs="*")
    s.set_defaults(fn=cmd_hook)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
