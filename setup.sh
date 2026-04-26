#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "=== Claw Cutter setup ==="
echo ""

# ── Python venv ─────────────────────────────────────────────────────────────
echo "[ 1/4 ] Setting up Python virtual environment…"
python3 -m venv .venv
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "        ✓ Python dependencies installed"
echo ""

# ── Frontend ─────────────────────────────────────────────────────────────────
echo "[ 2/4 ] Installing frontend dependencies…"
cd "$ROOT/frontend"
npm install --silent
cd "$ROOT"
echo "        ✓ Node dependencies installed"
echo ""

# ── models.json ──────────────────────────────────────────────────────────────
echo "[ 3/4 ] Checking backend/models.json…"
MODELS_FILE="$ROOT/backend/models.json"
if [ -f "$MODELS_FILE" ]; then
    echo "        ✓ Already exists — skipping"
else
    cat > "$MODELS_FILE" << 'EOF'
{
  "models": [
    {
      "id": "local-ollama",
      "name": "Ollama (local)",
      "provider": "openai_compat",
      "base_url": "http://localhost:11434/v1",
      "api_key": "",
      "model": "llama3.1:8b",
      "enabled": true,
      "preference": 1,
      "timeout_secs": 120,
      "max_tokens": 2048,
      "max_concurrent": 1,
      "extra_headers": {}
    },
    {
      "id": "claude-haiku",
      "name": "Claude Haiku",
      "provider": "anthropic",
      "base_url": "",
      "api_key": "YOUR_ANTHROPIC_API_KEY",
      "model": "claude-haiku-4-5-20251001",
      "enabled": false,
      "preference": 2,
      "timeout_secs": 60,
      "max_tokens": 2048,
      "max_concurrent": 1,
      "extra_headers": {}
    }
  ]
}
EOF
    echo "        ✓ Created with example entries — edit backend/models.json to configure your models"
fi
echo ""

# ── .env ─────────────────────────────────────────────────────────────────────
echo "[ 4/4 ] Checking backend/.env…"
ENV_FILE="$ROOT/backend/.env"
if [ -f "$ENV_FILE" ]; then
    echo "        ✓ Already exists — skipping"
else
    cat > "$ENV_FILE" << 'EOF'
# Shared access token shown to users on first visit.
# Leave blank to disable auth entirely.
APP_TOKEN=changeme

# Directory where uploaded files and job outputs are stored.
DATA_DIR=./data
EOF
    echo "        ✓ Created — edit backend/.env to set your APP_TOKEN"
fi
echo ""

# ── Done ─────────────────────────────────────────────────────────────────────
echo "=== Setup complete ==="
echo ""
echo "  Next steps:"
echo "    1. Edit backend/models.json  — add your model endpoint(s)"
echo "    2. Edit backend/.env         — set APP_TOKEN (or leave blank to disable auth)"
echo "    3. Run the app:"
echo ""
echo "         ./run.sh"
echo ""
