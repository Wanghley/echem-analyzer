#!/usr/bin/env bash
# Tag a release:  scripts/release.sh X.Y.Z [--push] [--skip-tests]
#
# Before running: bump __version__ in echem_analyzer/__init__.py, move the Unreleased notes under a new
# "## [X.Y.Z] - YYYY-MM-DD" heading in CHANGELOG.md, and commit both ("chore(release): X.Y.Z").
# The script refuses to tag unless all of that is true, runs the tests, then creates an annotated tag vX.Y.Z whose
# message is that changelog section. With --push it pushes main and only that tag.
set -euo pipefail
cd "$(dirname "$0")/.."

die() { echo "release: $*" >&2; exit 1; }

VERSION="${1:-}"; shift || true
PUSH=0; TESTS=1
for a in "$@"; do
  case "$a" in
    --push) PUSH=1 ;;
    --skip-tests) TESTS=0 ;;
    *) die "unknown option $a" ;;
  esac
done

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "usage: scripts/release.sh X.Y.Z [--push] [--skip-tests]"
TAG="v$VERSION"

[[ "$(git rev-parse --abbrev-ref HEAD)" == "main" ]] || die "releases are tagged from main"
[[ -z "$(git status --porcelain)" ]] || die "the working tree is not clean: commit or stash first"
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && die "tag $TAG already exists"

PKG_VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' echem_analyzer/__init__.py)"
[[ "$PKG_VERSION" == "$VERSION" ]] || die "__version__ is $PKG_VERSION, not $VERSION: bump echem_analyzer/__init__.py and commit"

NOTES="$(awk -v v="$VERSION" '
  $0 ~ "^## \\[" v "\\]" { on = 1; next }
  on && /^## \[/ { exit }
  on { print }' CHANGELOG.md | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}')"
[[ -n "${NOTES//[[:space:]]/}" ]] || die "CHANGELOG.md has no section (or an empty one) for [$VERSION]"

if [[ $TESTS -eq 1 ]]; then
  echo "release: running the tests…"
  export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.cache/echem-analyzer/venv}"
  uv run pytest -q -p no:warnings
fi

git tag -a "$TAG" -m "Echem Analyzer $VERSION" -m "$NOTES"
echo "release: created $TAG on $(git rev-parse --short HEAD)"

if [[ $PUSH -eq 1 ]]; then
  git push origin main "$TAG"
  echo "release: pushed main and $TAG"
else
  echo "release: not pushed. To publish:  git push origin main $TAG"
fi
