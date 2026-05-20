#!/bin/bash
set -euo pipefail

# Only run in remote cloud environments.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Install Python dependencies.
pip install -q -r "$CLAUDE_PROJECT_DIR/requirements.txt"

# Download Karpathy coding guidelines globally so they apply to every repo
# opened in this session, not just this one.
mkdir -p ~/.claude
curl -fsSL https://raw.githubusercontent.com/forrestchang/andrej-karpathy-skills/main/CLAUDE.md \
  -o ~/.claude/CLAUDE.md
