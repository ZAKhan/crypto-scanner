#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  Crypto Scalper Scanner — Release Push Script
#  Usage: ./push_release.sh
# ═══════════════════════════════════════════════════════════

REPO_DIR="/home/zulfiqar/apps/cryptoscanner"

GREEN="\033[92m"
YELLOW="\033[93m"
RED="\033[91m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

cd "$REPO_DIR" || { echo -e "${RED}ERROR: Cannot access $REPO_DIR${RESET}"; exit 1; }

# ── Files that are always included ──────────────────────────
CORE_FILES=(
    crypto_scanner_v9.py
    build.sh
    push_release.sh
    crypto_scanner.desktop
)

# ── Docs — only staged if they exist and were modified ──────
DOC_FILES=(
    README.html
    README.md
    crypto_scanner_guide.pdf
    crypto_scanner_guide.odt
    ANALYSIS.md
    PICOCLAW_WHATSAPP_SETUP.md
)

# ── Ask for version ─────────────────────────────────────────
read -p "Enter version number (e.g. 9.2): " VERSION
TAG="v$VERSION"

if git tag | grep -q "^$TAG$"; then
    echo -e "${RED}ERROR: Tag $TAG already exists. Choose a different version.${RESET}"
    exit 1
fi

# ── Ask for commit message ───────────────────────────────────
read -p "Commit message (or Enter for 'release $TAG'): " MSG
MSG="${MSG:-release $TAG}"

# ── Stage core files ─────────────────────────────────────────
echo ""
echo -e "${BOLD}Staging core files:${RESET}"
for f in "${CORE_FILES[@]}"; do
    if [[ -f "$f" ]]; then
        git add "$f"
        echo -e "  ${GREEN}+${RESET} $f"
    else
        echo -e "  ${YELLOW}?${RESET} $f  (not found — skipped)"
    fi
done

# ── Stage doc files only if modified ─────────────────────────
echo ""
echo -e "${BOLD}Staging modified docs:${RESET}"
for f in "${DOC_FILES[@]}"; do
    if [[ -f "$f" ]]; then
        git add "$f"
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

# ── Show what will be committed ──────────────────────────────
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
echo -e "${GREEN}${BOLD}Done!${RESET} GitHub Actions is now building binaries for ${CYAN}$TAG${RESET}."
echo ""
echo -e "  Build progress : https://github.com/ZAKhan/crypto-scanner/actions"
echo -e "  Create release : https://github.com/ZAKhan/crypto-scanner/releases/new?tag=$TAG"
echo ""
