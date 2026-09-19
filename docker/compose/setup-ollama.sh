#!/usr/bin/env bash
# Configure the Ollama service on the Docker HOST for the paperless AI chat.
#
#   sudo ./setup-ollama.sh [model]
#
# The model defaults to $AI_MODEL, then gemma4:e4b. Keep AI_CONTEXT_WINDOW in
# sync with .env.prod: a different context size makes Ollama reload the model.
set -euo pipefail

MODEL="${1:-${AI_MODEL:-gemma4:e4b}}"
CTX="${AI_CONTEXT_WINDOW:-8192}"
API="http://127.0.0.1:11434"

[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo $0" >&2; exit 1; }

command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh

install -d /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf <<EOF
[Service]
# Reachable from the containers through host.docker.internal.
Environment="OLLAMA_HOST=0.0.0.0:11434"
# Never unload the model (default is 5 minutes idle, then a slow reload).
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
# Two chats at once; every extra slot multiplies KV-cache memory and splits CPU.
Environment="OLLAMA_NUM_PARALLEL=2"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
Environment="OLLAMA_CONTEXT_LENGTH=${CTX}"
EOF

systemctl daemon-reload
systemctl enable ollama >/dev/null 2>&1 || true
systemctl restart ollama
until curl -fs "$API/api/version" >/dev/null; do sleep 1; done

# Ollama has no authentication. Only the Docker networks may reach it.
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  if ! ufw status | grep -q "11434"; then
    ufw insert 1 allow from 172.16.0.0/12 to any port 11434 proto tcp
    ufw insert 2 deny 11434/tcp
  fi
else
  echo "WARNING: ufw is not active, so port 11434 may be reachable from the internet." >&2
  echo "         Enable ufw, or block 11434 in your provider's firewall." >&2
fi

ollama pull "$MODEL"

# Load the model now and keep it resident, using the same context size the
# application requests (a different one would trigger a reload).
curl -fs "$API/api/generate" \
  -d "{\"model\":\"$MODEL\",\"prompt\":\"\",\"keep_alive\":-1,\"options\":{\"num_ctx\":$CTX}}" >/dev/null

echo "Benchmark ($MODEL, ctx $CTX):"
curl -fs "$API/api/generate" \
  -d "{\"model\":\"$MODEL\",\"prompt\":\"Summarize in one sentence why the sky is blue.\",\"stream\":false,\"keep_alive\":-1,\"options\":{\"num_ctx\":$CTX,\"num_predict\":64}}" |
  python3 -c '
import json, sys
r = json.load(sys.stdin)
gen = r["eval_count"] / (r["eval_duration"] / 1e9)
pre = r["prompt_eval_count"] / (r["prompt_eval_duration"] / 1e9)
load = r["load_duration"] / 1e9
print(f"  generation: {gen:.1f} tokens/s")
print(f"  prompt:     {pre:.1f} tokens/s")
print(f"  load time:  {load:.1f} s")
'
echo "Generation under ~5 tokens/s feels sluggish; try a smaller model (e.g. qwen3:4b, gemma3:4b)."
