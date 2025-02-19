#!/usr/bin/env bash
# A simple script to bump version, e.g., reading from a file or incrementing patch

VERSION_FILE="version.txt"

if [[ ! -f $VERSION_FILE ]]; then
  echo "0.0.0" > $VERSION_FILE
fi

oldver=$(cat $VERSION_FILE)
IFS='.' read -ra parts <<< "$oldver"
major=${parts[0]}
minor=${parts[1]}
patch=${parts[2]}

patch=$((patch+1))
newver="$major.$minor.$patch"

echo "$newver" > $VERSION_FILE
echo "Old version: $oldver"
echo "New version: $newver"
