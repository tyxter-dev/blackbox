# Releasing `tyxter-blackbox`

Pushing a `vX.Y.Z` tag runs `.github/workflows/release.yml`. The workflow
checks that the tag matches `project.version`, reruns the locked quality gates,
builds one wheel and source distribution, publishes those artifacts to PyPI,
then attaches the same files to a GitHub Release.

## One-time PyPI setup

In the `tyxter-blackbox` project's PyPI settings, add a GitHub Actions Trusted
Publisher with these values:

- Owner: `tyxter-dev`
- Repository: `blackbox`
- Workflow filename: `release.yml`
- Environment name: `pypi`

In the GitHub repository settings, create the `pypi` environment. Leaving it
without required reviewers makes tag releases fully automatic; adding reviewers
turns the PyPI upload into an explicit approval gate. The workflow uses OIDC
Trusted Publishing, so it needs no stored PyPI API token.

## Cutting a release

1. Update `project.version` in `pyproject.toml`, regenerate `uv.lock`, and run
   the normal quality gates.
2. Merge the release version commit to `master`.
3. Create and push the matching annotated tag:

   ```bash
   git tag -a vX.Y.Z -m "Release vX.Y.Z"
   git push origin vX.Y.Z
   ```

The tag must be exactly `v` followed by the `project.version` value. PyPI
versions are immutable, and the workflow is deliberately idempotent for a
re-run: it skips an existing PyPI upload and updates the GitHub Release assets.

GitHub Releases hold the built Python artifacts for auditing and direct
downloads. PyPI remains the sole install registry; GitHub Packages does not
provide a Python package registry.
