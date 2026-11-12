# Release checklist

Releases are published to PyPI as [`rastro-engine`](https://pypi.org/project/rastro-engine/) by
the tag-triggered workflow `.github/workflows/release.yml`. It uses PyPI Trusted Publishing:
there are no API tokens or repository secrets. Versions before 0.6.0 were never published.

The [release evidence register](release-evidence.md) records completed checks; the
[comparison](comparison.md) publishes current results, including where Rastro is equal or worse.
The [stability policy](stability.md) defines what a version number promises.

## What the workflow does

On a pushed tag `v*`:

1. **build** (no special permissions): checks that the tag equals `v` + the version in
   `pyproject.toml` and that `CHANGELOG.md` has a `## <version>` section, builds the sdist and
   wheel with `uv build`, runs `twine check --strict`, installs the wheel into a clean virtual
   environment and runs `rastro --version`, `rastro demo` and `import rastro` outside the
   checkout, then uploads `dist/` as a workflow artifact.
2. **publish** (environment `pypi`, permission `id-token: write` only): uploads the artifact to
   PyPI with `pypa/gh-action-pypi-publish` and PEP 740 attestations. This job waits for the
   environment's required reviewer.
3. **github-release** (permission `contents: write`): creates the GitHub release for the tag,
   with the changelog section as notes and the distributions attached.

All third-party actions are pinned by full commit SHA. If any step fails, nothing later runs; a
failure in **build** publishes nothing.

## One-time setup (repository owner)

1. On PyPI, open *Your account → Publishing → Add a new pending publisher* and enter:
   - PyPI project name: `rastro-engine`
   - Owner: `rave-vipx`
   - Repository name: `rastro`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
2. On GitHub, open *Settings → Environments → New environment*, name it `pypi`, enable
   *Required reviewers* and add yourself. Optionally restrict deployment to tags matching `v*`.

The pending publisher turns into a normal Trusted Publisher with the first successful upload.

## Each release

1. Merge the release changes into `main` through a reviewed pull request, with CI green
   (including the `package` job, which installs the wheel with and without extras).
2. In that pull request, `pyproject.toml` carries the version, `uv.lock` is regenerated
   (`uv lock`), and `CHANGELOG.md` has the final section: fold the `## Unreleased` entries into
   `## X.Y.Z — YYYY-MM-DD` and remove any "unreleased" wording. The workflow publishes that
   section as the release notes. SDK and CLI read the package metadata; there is no separate
   version constant.
3. Tag the exact merged commit on `main` and push the tag:

   ```bash
   git fetch origin
   git tag v0.6.0 <main sha>
   git push origin v0.6.0
   ```

4. Approve the `publish` job in the Actions tab when the build job has passed.
5. Verify the release: `pip install rastro-engine==0.6.0` in a fresh environment, then
   `rastro --version` and `rastro demo`. Check the project page shows the attestations.

Published tags and artifacts are immutable. Never move or re-push a tag; fix a bad release with a
new patch version. PyPI does not allow re-uploading a deleted version number.

Documentation deploys from `main` to GitHub Pages. A documentation deployment is not a package
release.
