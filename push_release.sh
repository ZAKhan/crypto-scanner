#!/bin/bash

REPO_DIR="/home/zulfiqar/apps/cryptoscanner"

cd "$REPO_DIR" || { echo "ERROR: Cannot access $REPO_DIR"; exit 1; }

# Ask for version
read -p "Enter version number (e.g. 1.1): " VERSION
TAG="v$VERSION"

# Check if tag already exists
if git tag | grep -q "^$TAG$"; then
    echo "ERROR: Tag $TAG already exists. Choose a different version."
    exit 1
fi

# Ask for commit message
read -p "Commit message (or press Enter for 'release $TAG'): " MSG
MSG="${MSG:-release $TAG}"

# Show what will be committed
echo ""
echo "Changed files:"
git status --short
echo ""

read -p "Proceed with commit, push and tag $TAG? (y/n): " CONFIRM
[[ "$CONFIRM" != "y" ]] && echo "Aborted." && exit 0

# Commit and push
git add -A
git commit -m "$MSG"
git push

# Tag and push tag
git tag "$TAG"
git push origin "$TAG"

echo ""
echo "Done! GitHub Actions is now building binaries for $TAG."
echo "Check progress at: https://github.com/ZAKhan/crypto-scanner/actions"
echo ""
echo "Once builds finish, create the release at:"
echo "https://github.com/ZAKhan/crypto-scanner/releases/new?tag=$TAG"
