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

# Stage everything tracked/untracked (the .gitignore already excludes tools/,
# build/, .venv/, third_party/, brainstorm/, and *.md scratch docs).
git add -A

# --- commit message: FreePDK45 (Nangate45) migration -------------------------
git commit -m "Switch synthesis flow from SkyWater 130nm to FreePDK45 (Nangate45)

Main SC flow (sc_flow.py) now loads freepdk45_demo instead of
skywater130_demo, reporting Nangate45 PPA. Move the shareability
experiment out of brainstorm/ into experiments/ and migrate it to
Nangate45. Gitignore brainstorm/ scratch and stop tracking the vendored
13MB Liberty and generated RTL (repo 13MB -> 284KB). Verified end-to-end:
demo_alu ~809um^2 and lowRISC Ibex ALU ~963um^2, both timing-met."

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
