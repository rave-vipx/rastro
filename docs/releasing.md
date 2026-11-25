# Release checklist

Releases are published to PyPI as [`rastro-engine`](https://pypi.org/project/rastro-engine/) by
the tag-triggered workflow `.github/workflows/release.yml`. It uses PyPI Trusted Publishing:
there are no API tokens or repository secrets. Versions before 0.6.0 were never published.

The [comparison](comparison.md) publishes measured results, including where Rastro is equal or worse.
The [stability policy](stability.md) defines what a version number promises.

## What the workflow does

On a pushed tag `v*`:

1. **build** (no special permissions): requires the tagged commit to be on `main`, or, for a
   patch tag, on the maintenance branch of its line (see [Branches](#branches)); checks that the
   tag equals `v` + the version in `pyproject.toml` and that `CHANGELOG.md` has a
   `## <version>` section, builds the sdist and wheel with `uv build`, runs
   `twine check --strict`, installs the wheel into a clean virtual environment and runs
   `rastro --version`, `rastro demo` and `import rastro` outside the checkout, then uploads
   `dist/` as a workflow artifact.
2. **publish** (environment `pypi`, permission `id-token: write` only): uploads the artifact to
   PyPI with `pypa/gh-action-pypi-publish` and PEP 740 attestations. This job waits for the
   environment's required reviewer.
3. **github-release** (permission `contents: write`): creates the GitHub release for the tag,
   with the changelog section as notes and the distributions attached.

All third-party actions are pinned by full commit SHA. If any step fails, nothing later runs; a
failure in **build** publishes nothing.

## Branches

`main` is where every change lands, and it stays releasable; there is no feature freeze before a
release. Feature work on the next minor continues there while the current line is patched.

A **maintenance branch** exists only to publish patch releases of a line that is already out.
`release/1.0` is cut from the commit tagged `v1.0.0`, lives next to `main`, and receives nothing
but cherry-picked fixes. `release/1.1` is cut the same way if `1.1.x` needs patches while `main`
moves on to `1.2`.

The first step of the release workflow enforces the split:

- a major or minor tag (`vX.Y.0`) must point at a commit on `main`;
- a patch tag (`vX.Y.Z`, `Z >= 1`) must point at a commit on `main` or on `release/X.Y`.

A tag that is not `v<major>.<minor>.<patch>`, or a patch tag whose branch does not exist (for
example `v1.2.1` with no `release/1.2`), fails before anything is built. Delete an unpublished
tag and tag the right commit again; never move a tag that PyPI already serves.

## One-time setup (repository maintainers)

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

A major or minor release is cut from `main`. A patch release follows the path in
[Patch releases](#patch-releases-10x) instead.

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
   git tag vX.Y.Z <main sha>
   git push origin vX.Y.Z
   ```

4. Approve the `publish` job in the Actions tab when the build job has passed.
5. Verify the release: `pip install rastro-engine==X.Y.Z` in a fresh environment, then
   `rastro --version` and `rastro demo`. Check the project page shows the attestations.

Published tags and artifacts are immutable. Never move or re-push a tag; fix a bad release with a
new patch version. PyPI does not allow re-uploading a deleted version number.

Documentation deploys from `main` to GitHub Pages. A documentation deployment is not a package
release.

## Patch releases (1.0.x)

A patch release fixes defects in a line that is already published; it never adds a job key, a
flag, a public name, a schema field or a change in meaning (see the
[stability policy](stability.md)). The example below patches `1.0.x` while `main` already carries
features for `1.1.0`: a date-parsing regression reported against `1.0.0` is fixed on `main`,
cherry-picked to `release/1.0`, given `version = "1.0.1"` and its changelog section there, tagged
`v1.0.1` from that branch, and `main` keeps working towards `1.1.0` untouched.

1. **Land the fix on `main` first**, as a pull request with its regression test and a changelog
   entry under `## Unreleased`. When the fix cannot be expressed on `main` because the newer code
   no longer has the defect, land it on the maintenance branch and open the pull request to
   `main` in the same week, so the two lines never drift.
2. **Cherry-pick; never merge.** In a pull request that targets `release/1.0`:

   ```bash
   git switch release/1.0
   git pull --ff-only
   git switch -c patch/1.0.1
   git cherry-pick -x <fix sha>
   ```

   `-x` records the original commit in the message. Resolve a conflict by re-applying the fix to
   the older code; never copy a newer file across wholesale and never merge `main` into a
   maintenance branch.
3. **Collect only what belongs to the patch.** Each commit in the pull request is a bug, test or
   documentation fix. Add the changelog section for the patch version (`## X.Y.Z — YYYY-MM-DD`),
   bump `pyproject.toml` and regenerate `uv.lock` with `uv lock`. The release notes come from
   that section, so they list the bugs and nothing else.
4. **Wait for CI on the release branch.** The same checks as `main` must pass: lint, types, the
   offline suite, the documentation build and the package install checks.
5. **Merge, then tag the merge commit** on `release/1.0`:

   ```bash
   git switch release/1.0
   git pull --ff-only
   git tag vX.Y.Z <release/1.0 merge sha>
   git push origin vX.Y.Z
   ```

6. **Approve and verify** the publish, exactly as for a minor release (steps 4 and 5 of
   [Each release](#each-release)).
7. **Keep the lines in sync.** The fix is already on `main` from step 1; nothing is merged back.
   If the maintenance branch was the only home of the fix, open the forward pull request before
   the patch is called done.

`release/1.0` is retired once no supported line needs it: stop tagging from it, and remove the
branch after the last patch release of the line has been verified.

