#!/bin/bash
# Rebuild the Library from Substack and publish it. Run this after every new post goes live:
#     bash library/update.sh
# Substack blocks GitHub's servers, so the fetch runs here; GitHub only publishes what this pushes.
set -euo pipefail
cd "$(dirname "$0")"
python3 -I build_library.py
if git diff --quiet -- data.json shelves.json build_library.py img fonts; then
  echo "Nothing changed on Substack since the last build, so there's nothing to publish."
  exit 0
fi
git add data.json shelves.json build_library.py img fonts
git commit -qm "Update the Library from Substack"
git push -q
echo "Pushed. GitHub publishes it in about a minute: https://chaharnishant11.github.io/ai-simplified-library/"
