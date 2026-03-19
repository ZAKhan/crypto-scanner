#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  cleanup.sh — Move junk to backup, leave only project files
# ─────────────────────────────────────────────────────────────

REPO_DIR="$(pwd)"
BACKUP_DIR="${REPO_DIR}/../cryptoscanner_backup_$(date +%Y%m%d_%H%M%S)"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; DIM='\033[0;90m'; RESET='\033[0m'

echo -e "${GREEN}━━━ Crypto Scanner Cleanup ━━━${RESET}"
echo -e "Backup : $BACKUP_DIR"
echo ""

KEEP=(
    "app_icon.png" "build.sh" "push_release.sh" "cleanup.sh"
    "requirements.txt" "README.md" "tutorial.html"
    "crypto_scanner_guide.odt" "crypto_scanner.desktop"
    "crypto_scanner.py" "CHANGELOG_v2.4.0.html"
    ".git" ".github" ".gitignore"
)

echo -e "${YELLOW}Will move to backup:${RESET}"
TO_MOVE=()
for item in "$REPO_DIR"/* "$REPO_DIR"/.[!.]*; do
    [[ ! -e "$item" ]] && continue
    name="$(basename "$item")"
    keep=false
    for k in "${KEEP[@]}"; do [[ "$name" == "$k" ]] && keep=true && break; done
    if [[ "$keep" == false ]]; then
        TO_MOVE+=("$item")
        echo -e "  ${DIM}→${RESET}  $name"
    fi
done

[[ ${#TO_MOVE[@]} -eq 0 ]] && echo "Nothing to move." && exit 0

echo ""
read -p "Proceed? (y/n): " confirm
[[ "$confirm" != "y" ]] && echo "Aborted." && exit 0

mkdir -p "$BACKUP_DIR"
for item in "${TO_MOVE[@]}"; do
    mv -- "$item" "$BACKUP_DIR/" && echo "  moved  $(basename "$item")"
done

echo ""
echo -e "${GREEN}Done — $(ls "$BACKUP_DIR" | wc -l) items backed up.${RESET}"
echo ""
ls -lh "$REPO_DIR"
echo ""
echo -e "${YELLOW}Commit:${RESET} git add -A && git commit -m 'chore: clean up repo' && git push"
