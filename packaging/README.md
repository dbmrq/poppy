# Packaging and releases

The distribution is `poppy-ai` (the PyPI name `poppy` is taken); the console
script is `poppy`. Releases are tag-driven.

## One-time setup

1. **PyPI trusted publishing.** Create a *pending* trusted publisher for the
   project `poppy-ai` (PyPI → Your account → Publishing):
   - Owner: `dbmrq`, repository: `poppy`, workflow: `release.yml`, environment: `pypi`.
2. **GitHub environment.** In the repo settings, create an environment named
   `pypi` (no secrets needed — the workflow authenticates with OIDC).
3. **Homebrew tap.** Already set up: the formula lives in the existing
   [`dbmrq/homebrew-tap`](https://github.com/dbmrq/homebrew-tap) tap at
   `Formula/poppy-ai.rb`.

## Cutting a release

```bash
# 1. bump the version
$EDITOR src/poppy/__init__.py
git commit -am "release: v0.2.0"

# 2. tag and push
git tag v0.2.0
git push origin main v0.2.0
```

The `release` workflow then:

1. checks that the tag matches `poppy.__version__`,
2. builds the sdist and wheel,
3. publishes to PyPI with trusted publishing,
4. renders the Homebrew formula (`packaging/homebrew/poppy-ai.rb`) from the
   sdist's PyPI URL and sha256,
5. creates a GitHub release with the artifacts.

## Homebrew

The formula lives in the [`dbmrq/homebrew-tap`](https://github.com/dbmrq/homebrew-tap)
tap. After a release, update it from the release artifacts:

```bash
cd <tap checkout>
gh release download vX.Y.Z --repo dbmrq/poppy --pattern poppy-ai.rb --dir /tmp --clobber
cp /tmp/poppy-ai.rb Formula/poppy-ai.rb
git commit -am "Update poppy-ai to vX.Y.Z" && git push
```

Users install with:

```bash
brew tap dbmrq/tap && brew install poppy-ai
```

Bump the template's `depends_on "python@X.Y"` as Homebrew's default Python
moves. `pipx install poppy-ai` remains the recommended path.

## If a release fails part-way

PyPI files are immutable, so never re-run the whole workflow after a successful
publish. Fix the workflow, then create the GitHub release from the run's
artifacts:

```bash
gh run download <run-id> -D /tmp/release-artifacts
gh release create "vX.Y.Z" /tmp/release-artifacts/dist/* /tmp/release-artifacts/homebrew/*.rb \
  --generate-notes --title "vX.Y.Z" --repo dbmrq/poppy
```

## Local dry run

```bash
python3 -m build
python3 packaging/check_version.py "v$(python3 -c 'from poppy import __version__; print(__version__)')"
python3 packaging/render_homebrew_formula.py dist/*.tar.gz
```
