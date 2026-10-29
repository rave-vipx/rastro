# Release checklist

No package release has been published. `0.1.0.dev0` identifies the in-development foundation and does not indicate that all v0.1 requirements are met.

The [release evidence register](release-evidence.md) records completed checks and open gates; the [comparison](comparison.md) publishes current results without claiming an advantage. M5 remains in progress.

Before a release:

1. Verify all release acceptance criteria in the master specification. Update the requirement coverage table and document remaining limitations.
2. Publish the reproducible Scrapling comparison methodology and results. Keep unsupported claims out of release notes.
3. Review dependency licenses, package/brand naming, and distribution ownership. `rastro-engine` is provisional and is not reserved by repository creation.
4. Run all contribution checks and the supported Python/OS CI matrix. Inspect built artifacts, install the wheel in a clean environment, and audit the resolved runtime dependency graph.
5. Update the single package version in `pyproject.toml`, regenerate `uv.lock`, and curate the changelog. SDK and CLI read package metadata; they have no separate version constant.
6. Merge a reviewed release PR. Tag the exact commit `vX.Y.Z`, matching package metadata. Treat published tags and artifacts as immutable.
7. Configure PyPI Trusted Publishing with a protected GitHub environment and provenance. Add a release workflow only when the package namespace and release gates are ready; this repository does not automatically publish development commits.
8. Publish the release notes and verify installation from the registry.

Documentation deploys from `main` to GitHub Pages. A documentation deployment is not a package release.
