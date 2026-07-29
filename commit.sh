#!/usr/bin/env bash
# ============================================================================
#  commit.sh -- commit + push the current research changes.
#
#  This is the standing place for the git commands to run. Run it from the
#  repo root:
#       bash commit.sh
#  (or make it executable once with `chmod +x commit.sh` and run `./commit.sh`)
#
#  It commits under YOUR git identity (Dennis Binford) with no AI attribution.
#  Review `git status` / `git diff` first if you want to see what will go in.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

# What is actually going in this commit (since 498285b, the FreePDK45 switch):
#   * toolchain.py -- select_cxx() now auto-detects a coroutine-capable C++
#     compiler (see message below). This is the ONLY tracked change.
# Note: README.md was also edited locally (FU_CXX is now optional), but *.md is
# gitignored ("working docs -- keep local, do not publish"), so it is NOT
# published. Run `git status` to confirm what will be staged.

# Stage everything tracked/untracked (the .gitignore already excludes tools/,
# build/, .venv/, third_party/, brainstorm/, and *.md scratch docs).
git add -A

# --- commit message: C++ compiler auto-detection -----------------------------
git commit -m "Up to date FreePDK45 with PPA numbers ran"

# --- push --------------------------------------------------------------------
# First push on a branch needs an upstream; after that a plain `git push` works.
if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
  git push
else
  git push -u origin main
fi

# --- confirm authorship (should be Dennis Binford, no Co-Authored-By) ---------
echo
echo "Last commit author:"
git log -1 --format='  %an <%ae> -- %s'
