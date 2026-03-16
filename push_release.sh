#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  Crypto Scalper Scanner — Release Push Script
#  Usage: ./push_release.sh
# ═══════════════════════════════════════════════════════════

# Always run from the directory the script lives in
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

GREEN="\033[92m"
YELLOW="\033[93m"
RED="\033[91m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

cd "$REPO_DIR" || { echo -e "${RED}ERROR: Cannot access $REPO_DIR${RESET}"; exit 1; }
echo -e "${CYAN}Repo: $REPO_DIR${RESET}"

# ── Files always included ────────────────────────────────────
CORE_FILES=(
    crypto_scanner.py
    app_icon.png
    push_release.sh
    requirements.txt
)

# ── Docs — staged only if modified ──────────────────────────
DOC_FILES=(
    README.md
    tutorial.html
    crypto_scanner_guide.odt
)

# ── Ask for version ──────────────────────────────────────────
read -p "Enter version number (e.g. 2.0.0): " VERSION
TAG="v$VERSION"

if git tag | grep -q "^$TAG$"; then
    echo -e "${RED}ERROR: Tag $TAG already exists. Choose a different version.${RESET}"
    exit 1
fi

# ── Ask for commit message ───────────────────────────────────
read -p "Commit message (or Enter for 'release $TAG'): " MSG
MSG="${MSG:-release $TAG}"

# ── Update APP_VERSION in crypto_scanner.py ─────────────────
echo ""
CURRENT_VER=$(grep -oP '(?<=APP_VERSION = ").*(?=")' crypto_scanner.py)
echo -e "${BOLD}Current APP_VERSION:${RESET} ${YELLOW}$CURRENT_VER${RESET}"

if [[ "$CURRENT_VER" != "$VERSION" ]]; then
    read -p "Update APP_VERSION to $VERSION in crypto_scanner.py? (y/n): " UPDATE_VER
    if [[ "$UPDATE_VER" == "y" ]]; then
        sed -i "s/APP_VERSION = \".*\"/APP_VERSION = \"$VERSION\"/" crypto_scanner.py
        echo -e "  ${GREEN}✓${RESET} APP_VERSION updated to $VERSION"
    fi
fi

# ── Stage core files ─────────────────────────────────────────
echo ""
echo -e "${BOLD}Staging core files:${RESET}"
for f in "${CORE_FILES[@]}"; do
    if [[ -f "$f" ]]; then
        git add "$f"
        echo -e "  ${GREEN}+${RESET} $f"
    else
        echo -e "  ${RED}✗${RESET} $f  (NOT FOUND)"
    fi
done

# ── Stage docs only if modified ──────────────────────────────
echo ""
echo -e "${BOLD}Staging modified docs:${RESET}"
for f in "${DOC_FILES[@]}"; do
    if [[ -f "$f" ]]; then
        git add -f "$f" 2>/dev/null
        STATUS=$(git diff --cached --name-only | grep -c "^$f$" || true)
        if [[ "$STATUS" -gt 0 ]]; then
            echo -e "  ${GREEN}+${RESET} $f  (modified)"
        else
            echo -e "  ${CYAN}–${RESET} $f  (unchanged, skipped)"
            git restore --staged "$f" 2>/dev/null || true
        fi
    else
        echo -e "  ${YELLOW}?${RESET} $f  (not found — skipped)"
    fi
done

# ── Show staged files ────────────────────────────────────────
echo ""
echo -e "${BOLD}Files in this commit:${RESET}"
git diff --cached --name-only | while read -r f; do
    echo -e "  ${GREEN}✓${RESET} $f"
done

STAGED=$(git diff --cached --name-only | wc -l)
if [[ "$STAGED" -eq 0 ]]; then
    echo -e "${YELLOW}Nothing staged — no changes to commit. Aborting.${RESET}"
    exit 0
fi

echo ""
echo -e "  Tag     : ${CYAN}${TAG}${RESET}"
echo -e "  Message : ${CYAN}${MSG}${RESET}"
echo ""

# ── Confirm ──────────────────────────────────────────────────
read -p "Proceed? (y/n): " CONFIRM
[[ "$CONFIRM" != "y" ]] && echo "Aborted." && exit 0

# ── Commit, push, tag ────────────────────────────────────────
git commit -m "$MSG"
git push

git tag "$TAG"
git push origin "$TAG"

echo ""
echo -e "${GREEN}${BOLD}Done!${RESET} Released ${CYAN}$TAG${RESET}."
echo ""
echo -e "  Build progress : https://github.com/ZAKhan/crypto-scanner/actions"
echo -e "  Create release : https://github.com/ZAKhan/crypto-scanner/releases/new?tag=$TAG"
echo ""
