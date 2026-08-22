# Versioning policy

Every user-visible feature or fix must include a version bump in the same pull request.

Keep these release metadata surfaces synchronized:

- `pyproject.toml` project version
- `src/synctify/__init__.py` runtime `__version__`
- `README.md` current version and release asset names
- `CHANGELOG.md` section for the new version
- release/version assertions in tests when they intentionally pin the current version

Use semantic patch/minor/major increments as appropriate. Do not create or move a release tag until the version-bump pull request is merged and CI is green.

The pull-request CI workflow enforces a version change when product code, release scripts, or distribution files change.
