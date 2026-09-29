#!/usr/bin/env bash
# Measure how fast the AI answers on this Ubuntu host, and where the time goes.
#
#   cd docker/compose && sudo ./ai-speed-check.sh
#
# Read-only: it changes nothing. Paste its output when asking for help.
set -uo pipefail

MODEL="${AI_MODEL:-$(grep -E '^AI_MODEL=' .env.prod 2>/dev/null | cut -d= -f2)}"
MODEL="${MODEL:-gemma4:e4b}"
CTX="${AI_CONTEXT_WINDOW:-$(grep -E '^AI_CONTEXT_WINDOW=' .env.prod 2>/dev/null | cut -d= -f2)}"
CTX="${CTX:-8192}"
API="http://127.0.0.1:11434"

section() { printf '\n== %s ==\n' "$1"; }

section "Machine"
echo "CPU:     $(lscpu | sed -n 's/^Model name:\s*//p') ($(nproc) cores visible)"
echo "Memory:  $(free -h | awk '/^Mem:/ {print $2 " total, " $7 " available"}')"
echo "Swap:    $(free -h | awk '/^Swap:/ {print $3 " used of " $2}')"
if [ "$(free -m | awk '/^Swap:/ {print $3}')" -gt 1024 ] 2>/dev/null; then
  echo "WARNING: over 1 GB of swap in use. If the model is swapped out, every answer is very slow."
fi
echo "Load:    $(cut -d' ' -f1-3 /proc/loadavg) (1/5/15 min)"

section "Ollama service"
if ! curl -fs "$API/api/version" >/dev/null; then
  echo "Ollama is NOT answering on $API. Start it: sudo systemctl start ollama"
  exit 1
fi
echo "Version: $(curl -fs "$API/api/version")"
systemctl show ollama -p Environment -p CPUWeight 2>/dev/null | tr ' ' '\n' | grep -E 'OLLAMA_|CPUWeight' | sed 's/^/  /'
echo "Loaded models (UNTIL should be 'Forever', PROCESSOR '100% CPU'):"
ollama ps 2>/dev/null | sed 's/^/  /'

section "Reachability from the containers"
if docker compose -f docker-compose.prod.yml --env-file .env.prod ps --status running webserver >/dev/null 2>&1; then
  if docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T webserver \
      curl -fs --max-time 5 http://host.docker.internal:11434/api/version >/dev/null; then
    echo "webserver -> Ollama: OK"
  else
    echo "webserver -> Ollama: FAILED. Check ufw allows the Docker subnet:"
    docker network inspect paperless-prod -f '  subnet: {{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null
    echo "  sudo ufw status | grep 11434"
  fi
else
  echo "webserver container not running; skipped."
fi

section "Benchmark ($MODEL, num_ctx $CTX)"
# About the size of a quick question's context: Arabic text, ~2400 characters.
CONTEXT=$(python3 - <<'PY'
text = ("تتضمن هذه الوثيقة وصفاً لنظام أرشفة المعلومات، ومكوناته التقنية، والجهات المسؤولة عن "
        "تشغيله وصيانته، والمهل الزمنية المتفق عليها، وقيمة العقد وشروط الدفع والضمان. ")
print((text * 20)[:2400])
PY
)
run() {
  python3 - "$MODEL" "$CTX" "$1" <<'PY'
import json, sys, time, urllib.request
model, ctx, context = sys.argv[1], int(sys.argv[2]), sys.argv[3]
body = {
    "model": model, "stream": True, "keep_alive": -1,
    "options": {"num_ctx": ctx, "num_predict": 48},
    "messages": [
        {"role": "system", "content": "أجب باختصار بالعربية اعتماداً على النص.\n" + context},
        {"role": "user", "content": "ما موضوع هذه الوثيقة؟"},
    ],
}
req = urllib.request.Request("http://127.0.0.1:11434/api/chat", json.dumps(body).encode(),
                             {"Content-Type": "application/json"})
start = time.monotonic(); first = None; final = {}
with urllib.request.urlopen(req, timeout=600) as r:
    for line in r:
        chunk = json.loads(line)
        if first is None and chunk.get("message", {}).get("content"):
            first = time.monotonic() - start
        if chunk.get("done"):
            final = chunk
total = time.monotonic() - start
pe, pd = final.get("prompt_eval_count", 0), final.get("prompt_eval_duration", 1) / 1e9
ec, ed = final.get("eval_count", 0), final.get("eval_duration", 1) / 1e9
print(f"  first word after: {first or total:6.1f} s")
print(f"  total:            {total:6.1f} s")
print(f"  model load:       {final.get('load_duration', 0) / 1e9:6.1f} s")
print(f"  prompt read:      {pe} tokens in {pd:.1f} s = {pe / pd if pd else 0:.0f} tokens/s")
print(f"  generation:       {ec} tokens in {ed:.1f} s = {ec / ed if ed else 0:.1f} tokens/s")
print(f"PROMPT_TPS={pe / pd if pd else 0:.0f}")
PY
}
echo "1st request (reads the whole prompt):"
FIRST=$(run "$CONTEXT"); echo "$FIRST" | grep -v '^PROMPT_TPS='
echo "2nd request, same document (should reuse Ollama's prompt cache):"
SECOND=$(run "$CONTEXT"); echo "$SECOND" | grep -v '^PROMPT_TPS='

TPS=$(echo "$FIRST" | sed -n 's/^PROMPT_TPS=//p')
section "Suggested settings for .env.prod"
if [ -n "$TPS" ] && [ "$TPS" -gt 0 ] 2>/dev/null; then
  # ~3 characters of Arabic per token; aim for the first word within ~6 s.
  SUGGEST=$(( TPS * 6 * 3 ))
  [ "$SUGGEST" -lt 1200 ] && SUGGEST=1200
  [ "$SUGGEST" -gt 4000 ] && SUGGEST=4000
  echo "AI_FAST_CONTEXT_CHARS=$SUGGEST   # context of a quick answer (now 2400 by default)"
  if [ "$TPS" -lt 40 ]; then
    echo "The model reads under 40 tokens/s here. For noticeably faster answers use a"
    echo "smaller model (e.g. qwen3:4b or gemma3:4b), after checking its Arabic quality."
  fi
fi
echo "If the 2nd request's 'prompt read' is not much shorter than the 1st, the prompt"
echo "cache is not being reused; report this output."
