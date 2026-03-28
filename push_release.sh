#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  Crypto Scalper Scanner — Release Push Script
#  Usage: ./push_release.sh
# ═══════════════════════════════════════════════════════════

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

GREEN="\033[92m"
YELLOW="\033[93m"
RED="\033[91m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

info()  { echo -e "${CYAN}${BOLD}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}${BOLD}[ OK ]${RESET}  $*"; }
warn()  { echo -e "${YELLOW}${BOLD}[WARN]${RESET}  $*"; }
err()   { echo -e "${RED}${BOLD}[ERR ]${RESET}  $*"; exit 1; }
step()  { echo -e "\n${BOLD}${CYAN}━━━ $* ━━━${RESET}"; }

cd "$REPO_DIR" || err "Cannot access $REPO_DIR"
echo -e "${CYAN}Repo: $REPO_DIR${RESET}"

# ── Pre-flight: verify all required files exist ──────────────
step "Pre-flight checks"

REQUIRED_FILES=(
    crypto_scanner.py
    cs/config.py
    build.sh
    requirements.txt
    README.md
    push_release.sh
)

OPTIONAL_FILES=(
    LICENSE
    CHANGELOG_v2_6_0.html
    CHANGELOG_v2_7_0.html
)

ALL_OK=true
echo -e "${BOLD}Required files:${RESET}"
for f in "${REQUIRED_FILES[@]}"; do
    if [[ -f "$f" ]]; then
        echo -e "  ${GREEN}✓${RESET}  $f"
    else
        echo -e "  ${RED}✗${RESET}  $f  ← MISSING"
        ALL_OK=false
    fi
done

echo ""
echo -e "${BOLD}Optional files:${RESET}"
for f in "${OPTIONAL_FILES[@]}"; do
    if [[ -f "$f" ]]; then
        echo -e "  ${GREEN}✓${RESET}  $f"
    else
        echo -e "  ${YELLOW}–${RESET}  $f  (not found — skipped)"
    fi
done

if [[ "$ALL_OK" == false ]]; then
    echo ""
    err "One or more required files are missing. Fix before releasing."
fi

# ── Check git status ─────────────────────────────────────────
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    err "Not a git repository. Run from the project directory."
fi

ok "All required files present"

# ── Files always included ────────────────────────────────────
CORE_FILES=(
    crypto_scanner.py
    build.sh
    push_release.sh
    cleanup.sh
    requirements.txt
)

# ── Docs — staged only if modified ──────────────────────────
DOC_FILES=(
    README.md
    LICENSE
    CHANGELOG_v2_6_0.html
    CHANGELOG_v2_7_0.html
    tutorial.html
)

# ── Ask for version ──────────────────────────────────────────
step "Version"

CURRENT_VER=$(grep -oP '(?<=APP_VERSION = ").*(?=")' cs/config.py 2>/dev/null || echo "unknown")
echo -e "  Current APP_VERSION in code: ${YELLOW}$CURRENT_VER${RESET}"
echo ""
read -p "Enter release version (e.g. 2.2.0): " VERSION
TAG="v$VERSION"

if git tag | grep -q "^$TAG$"; then
    err "Tag $TAG already exists. Choose a different version."
fi

# ── Update APP_VERSION if needed ─────────────────────────────
if [[ "$CURRENT_VER" != "v$VERSION" && "$CURRENT_VER" != "$VERSION" ]]; then
    echo ""
    read -p "Update APP_VERSION to v$VERSION in crypto_scanner.py? (y/n): " UPDATE_VER
    if [[ "$UPDATE_VER" == "y" ]]; then
        sed -i "s/APP_VERSION = \".*\"/APP_VERSION = \"v$VERSION\"/" cs/config.py
        ok "APP_VERSION updated to v$VERSION"
    fi
else
    ok "APP_VERSION already set to $CURRENT_VER"
fi

# ── Ask for commit message ───────────────────────────────────
echo ""
read -p "Commit message (or Enter for 'release $TAG'): " MSG
MSG="${MSG:-release $TAG}"

# ── Stage core files ─────────────────────────────────────────
step "Staging files"

echo -e "${BOLD}Core files:${RESET}"
for f in "${CORE_FILES[@]}"; do
    if [[ -f "$f" ]]; then
        git add "$f"
        echo -e "  ${GREEN}+${RESET} $f"
    else
        echo -e "  ${YELLOW}–${RESET} $f  (skipped — not found)"
    fi
done

# Stage the entire cs/ package
if [[ -d "cs" ]]; then
    git add cs/
    echo -e "  ${GREEN}+${RESET} cs/  (package)"
else
    echo -e "  ${RED}✗${RESET} cs/  ← MISSING — package not found"
fi

echo ""
echo -e "${BOLD}Docs (only if modified):${RESET}"
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

# ── Show what will be committed ──────────────────────────────
echo ""
echo -e "${BOLD}Files in this commit:${RESET}"
git diff --cached --name-only | while read -r f; do
    echo -e "  ${GREEN}✓${RESET} $f"
done

STAGED=$(git diff --cached --name-only | wc -l)
if [[ "$STAGED" -eq 0 ]]; then
    warn "Nothing staged — no changes to commit. Aborting."
    exit 0
fi

# ── Final confirmation ───────────────────────────────────────
step "Confirm"
echo -e "  Tag     : ${CYAN}${TAG}${RESET}"
echo -e "  Message : ${CYAN}${MSG}${RESET}"
echo -e "  Files   : ${CYAN}${STAGED} file(s)${RESET}"
echo ""
read -p "Proceed? (y/n): " CONFIRM
[[ "$CONFIRM" != "y" ]] && echo "Aborted." && exit 0

# ── Commit, push, tag ────────────────────────────────────────
step "Pushing"

git commit -m "$MSG"
git push

git tag "$TAG"
git push origin "$TAG"

step "Done"
echo -e "  Released : ${GREEN}${TAG}${RESET}"
echo -e "  Actions  : https://github.com/ZAKhan/crypto-scanner/actions"
echo -e "  Release  : https://github.com/ZAKhan/crypto-scanner/releases/new?tag=$TAG"
echo ""
