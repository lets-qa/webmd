# Homebrew packaging for webmd

`webmd.rb` is the formula. It is meant to live in a dedicated tap repo, not in this repo.

## Strategy: dedicated tap now, homebrew-core later

homebrew-core's Package Acceptance Policy requires, for a GitHub project, at least
30 forks, 30 watchers or 75 stars (90 / 90 / 225 when the author submits their own
project), and the repo must be at least 30 days old. `lets-qa/webmd` was created
2026-10-09 and has 0 stars and 0 forks, so core would reject it today.

So: publish `lets-qa/homebrew-tap` (users run `brew install lets-qa/tap/webmd`).
Revisit homebrew-core once the repo is over 30 days old and meets the threshold
(ideally via a third-party submission). The formula is already written in core style
(`depends_on "cryptography"`, no vendored resources), so moving it is mostly copy and
`brew audit --new`.

## How the formula works

- `Language::Python::Virtualenv` creates a venv in `libexec` with `--system-site-packages`.
  `pip install` runs with `--no-deps --no-binary=:all: --ignore-installed --no-compile`
  and build isolation ON for resources and the main package. So `pip` fetches hatchling
  from PyPI at build time to build the sdist; core formulae allow this for the project
  itself, and hatchling does not have to be listed as a resource (the `hatch` formula
  lists it only because it is a runtime dependency there).
- `cryptography` is NOT a resource. `depends_on "cryptography"` uses the homebrew-core
  formula (prebuilt bottle, Rust/OpenSSL already handled). It is exposed to the venv via
  `homebrew_deps.pth`. Building cryptography from source (Rust) is thereby avoided.
- `webmd` has no other runtime dependencies, so there are no `resource` blocks.
- 0.2.1 adds `src/webmd/static/` data files. They are inside the package, so hatchling
  includes them in the sdist/wheel and the formula needs only a new `url` + `sha256`.
  Check that `pyproject.toml`'s `[tool.hatch.build.targets.sdist] include` still covers
  `src/webmd` (it does today).
- Python coupling: `python@3.14` matches the python the core `cryptography` formula
  currently builds against (`depends_on "python@3.14"`). When core moves to 3.15,
  `brew audit` / cryptography's bottle will break the import; bump `python@3.x` then.

## License

From 0.2.1 the package bundles marked (MIT), DOMPurify (MPL-2.0 OR Apache-2.0),
highlight.js (BSD-3-Clause) and github-markdown-css (MIT), all installed with webmd,
so the formula declares (split across lines, as `brew style` requires):

    license all_of: [
      "MIT",
      "BSD-3-Clause",
      any_of: ["MPL-2.0", "Apache-2.0"],
    ]

## Current state of `webmd.rb`

The license and `test do` block are already updated for 0.2.1 (the test also checks
the bundled `purify.min.js` is served). `url`/`sha256` still point at the
**published 0.2.0 sdist** on purpose: 0.2.1 isn't on PyPI yet, and the formula must
only ever reference a released, tested artifact. Updating to 0.2.1 is step 2-3 of the
release procedure below. Note that 0.2.0's page doesn't serve `/__webmd__/static/`,
so the updated test is expected to fail against the 0.2.0 URL. Publish the tap
only after switching to the 0.2.1 sdist.

## Create the tap (one time)

    # on GitHub: create the public repo lets-qa/homebrew-tap (name MUST start with homebrew-)
    git clone https://github.com/lets-qa/homebrew-tap && cd homebrew-tap
    mkdir Formula && cp /path/to/web-md/packaging/homebrew/webmd.rb Formula/
    git add . && git commit -m "webmd 0.2.0" && git push

(`brew tap-new lets-qa/tap` also scaffolds the CI workflows for bottling, if wanted.
A pure-Python formula like this works without bottles; it builds in ~30s.)

## Release procedure (per webmd version)

1. Publish the new version to PyPI and smoke test it (`pipx install webmd==X.Y.Z`).
2. Get the sdist URL and sha256 from `https://pypi.org/pypi/webmd/X.Y.Z/json`
   (entry with `packagetype: sdist`).
3. Update `url` and `sha256` in `Formula/webmd.rb` (update `license` for 0.2.1 as above).
   Alternatively: `brew bump-formula-pr` is for core; for a tap use
   `brew bump-formula-pr --no-fork --commit` or simply edit by hand.
4. In the tap checkout: `brew install --build-from-source lets-qa/tap/webmd`,
   `brew test lets-qa/tap/webmd`, `brew audit --strict --new --online lets-qa/tap/webmd`
   (`--new` only for the first release; afterwards `--strict --online`).
5. Commit and push the tap. Users get it with `brew update && brew upgrade webmd`.
6. Keep `packaging/homebrew/webmd.rb` in this repo in sync (it is the source copy).

## User commands

    brew install lets-qa/tap/webmd      # also taps lets-qa/tap
    brew upgrade webmd
    brew uninstall webmd
    brew untap lets-qa/tap

## Verified in Docker (homebrew/brew, linux/arm64, Homebrew with python@3.14, cryptography 50.0.2_1)

Formula = 0.2.0 sdist. Local tap via `brew tap-new local/test`, formula copied into `Formula/`.

- `brew install --build-from-source local/test/webmd`: PASS (~30s for the formula;
  all dependencies poured from bottles; cryptography came from the core bottle).
- `brew install local/test/webmd`: PASS.
- `brew test local/test/webmd`: PASS (runs `--version`, starts the server on a free port,
  curls `/README.md`, checks content, kills the server).
- `brew audit --strict --new --online local/test/webmd`: PASS (no output, rc 0).
- `brew style local/test/webmd`: PASS (no offenses).
- `webmd --version` -> `webmd 0.2.0`; serving a temp dir and `curl /README.md` -> HTTP 200
  and the page HTML with the Markdown embedded.
- Venv `import cryptography` resolves to `/home/linuxbrew/.linuxbrew/opt/cryptography/...` (core formula,
  not vendored).
- `brew uninstall webmd`: PASS; `bin/webmd` symlink and Cellar entry removed.

### Re-verified with a locally built 0.2.1 sdist (2026-10-09)

Same container setup, with `url "file:///work/webmd-0.2.1.tar.gz"` and its sha256
(`uv build` output from the release/0.2.1 branch), plus the 0.2.1 license and test block:

- `brew install --build-from-source local/test/webmd`: PASS,
  `Cellar/webmd/0.2.1: 39 files, 437.7KB, built in 32 seconds`.
- `webmd --version` -> `webmd 0.2.1`.
- `brew test local/test/webmd`: PASS (`GET /README.md -> 200`,
  `GET /__webmd__/static/vendor/purify.min.js -> 200`).
- Installed package contains `webmd/static/vendor/{purify,marked,highlight}.min.js`.
- `brew audit --strict local/test/webmd`: PASS. (`--online`/`--new` can't run
  against a `file://` URL; run them once the PyPI sdist exists.)
- `brew style local/test/webmd`: PASS, once the nested license was split over lines.
- `brew uninstall webmd`: PASS; command gone, no Cellar entry left.

NOT verified: macOS installs (Linux container only); the real PyPI 0.2.1 sdist
(not published yet); installing from the real `lets-qa/tap` (not created).
