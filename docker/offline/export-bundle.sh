#!/usr/bin/env bash
# Builds a self-contained bundle to install the archive on a server that has
# Docker but no internet access (or should not need it): every image the
# stack runs, saved to files, plus the compose file, settings and installer.
#
#   docker/offline/export-bundle.sh [output-dir]
#
# Run from anywhere on the machine that has the source and Docker. Options
# (environment variables):
#   SKIP_BUILD=1            reuse the existing paperless-ngx-custom:latest
#   OLLAMA_MODELS_VOLUME=v  Docker volume holding the Ollama model to bundle
#                           (default: the first volume that has it)
#   AI_MODEL=gemma4:e4b     the model to bundle
#   NO_AI=1                 bundle without Ollama and the model (~11 GB less)
#   RESUME=1                keep image files already saved and verified
#
# The output directory then holds everything; copy it as a whole to the server
# and run ./install.sh there (see README.md in it).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
COMPOSE_DIR="$ROOT/docker/compose"
REV="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
OUT="${1:-$ROOT/../archive-offline-bundle-$REV}"
AI_MODEL="${AI_MODEL:-gemma4:e4b}"
OLLAMA_BASE="${OLLAMA_BASE:-ollama/ollama:0.40.16}"
OLLAMA_IMAGE="paperless-ollama:${AI_MODEL//:/-}"
PLATFORM=linux/amd64

step() { printf '\n==> %s\n' "$*"; }

if [ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]; then
  echo "WARNING: the working tree has uncommitted changes; they go into the image," >&2
  echo "         but the bundle is labelled with commit $REV." >&2
fi

mkdir -p "$OUT/images"
OUT="$(cd "$OUT" && pwd)"

# ── 1. The application image ────────────────────────────────────────────────
if [ "${SKIP_BUILD:-}" != 1 ]; then
  step "Building paperless-ngx-custom from $ROOT ($REV)"
  # docker build, not "compose build": compose's bake mode on Windows asks for
  # extra filesystem permissions and stops.
  docker build --platform "$PLATFORM" \
    -f "$COMPOSE_DIR/Dockerfile.prod" \
    -t paperless-ngx-custom:latest -t "paperless-ngx-custom:$REV" \
    "$ROOT"
fi

# ── 2. The AI image: Ollama with the model inside ───────────────────────────
if [ "${NO_AI:-}" != 1 ] && ! docker image inspect "$OLLAMA_IMAGE" >/dev/null 2>&1; then
  manifest="models/manifests/registry.ollama.ai/library/${AI_MODEL%%:*}/${AI_MODEL#*:}"
  volume="${OLLAMA_MODELS_VOLUME:-}"
  if [ -z "$volume" ]; then
    for v in $(docker volume ls -q); do
      if MSYS_NO_PATHCONV=1 docker run --rm -v "$v:/m:ro" alpine test -f "/m/$manifest" 2>/dev/null; then
        volume="$v"; break
      fi
    done
  fi
  [ -n "$volume" ] || {
    echo "No Docker volume holds $AI_MODEL. Pull it first, e.g.:" >&2
    echo "  docker run --rm -v ollama_models:/root/.ollama $OLLAMA_BASE sh -c 'ollama serve & sleep 3; ollama pull $AI_MODEL'" >&2
    echo "then rerun with OLLAMA_MODELS_VOLUME=ollama_models (or NO_AI=1)." >&2
    exit 1
  }
  step "Building $OLLAMA_IMAGE from $OLLAMA_BASE + volume $volume"
  docker pull --platform "$PLATFORM" "$OLLAMA_BASE"
  docker rm -f paperless-ollama-build >/dev/null 2>&1 || true
  # Only the models: Ollama creates its own key pair on first start.
  MSYS_NO_PATHCONV=1 docker run --name paperless-ollama-build -v "$volume:/src:ro" \
    --entrypoint sh "$OLLAMA_BASE" -c 'mkdir -p /root/.ollama && cp -a /src/models /root/.ollama/'
  docker commit \
    --change 'ENTRYPOINT ["/bin/ollama"]' \
    --change 'CMD ["serve"]' \
    --change 'ENV OLLAMA_HOST=0.0.0.0:11434' \
    --change 'ENV OLLAMA_KEEP_ALIVE=-1' \
    --change 'ENV OLLAMA_CONTEXT_LENGTH=8192' \
    --change "LABEL org.opencontainers.image.description=\"$OLLAMA_BASE with $AI_MODEL for the archive AI\"" \
    paperless-ollama-build "$OLLAMA_IMAGE"
  docker rm paperless-ollama-build >/dev/null
fi

# ── 3. Compose file, settings, installer ────────────────────────────────────
step "Writing docker-compose.yml"
overrides=(-f "$COMPOSE_DIR/docker-compose.prod.yml" -f "$HERE/docker-compose.offline.yml")
if [ "${NO_AI:-}" != 1 ]; then
  overrides+=(-f "$HERE/docker-compose.ollama.yml")
fi
# --no-interpolate keeps ${VARS} as they are: no secret of this machine's
# .env.prod ends up in the bundle. -p fixes the volume names (archive_*).
(cd "$COMPOSE_DIR" && docker compose -p archive "${overrides[@]}" config --no-interpolate) \
  | sed -e 's/must be set in \.env\.prod/must be set in .env/' \
        -e "s#paperless-ollama:gemma4-e4b#$OLLAMA_IMAGE#" \
        -e 's#^    name: paperless-prod$#    name: archive#' \
  > "$OUT/docker-compose.yml"

sed -e "s#^AI_MODEL=.*#AI_MODEL=$AI_MODEL#" "$HERE/env.example" > "$OUT/.env.example"
if [ "${NO_AI:-}" = 1 ]; then
  # Without the AI container, the app expects Ollama on the host.
  sed -i 's#^AI_ENDPOINT=.*#AI_ENDPOINT=http://host.docker.internal:11434#' "$OUT/.env.example"
fi
cp "$HERE/install.sh" "$OUT/install.sh"
cp "$HERE/README.md" "$OUT/README.md"
chmod +x "$OUT/install.sh"

# ── 4. Images ────────────────────────────────────────────────────────────────
images=$(cd "$OUT" && docker compose -p archive --env-file .env.example config --images | sort -u)
step "Pulling base images for $PLATFORM"
for image in $images; do
  case "$image" in
    paperless-ngx-custom:*|paperless-ollama:*) ;;
    *) docker pull --platform "$PLATFORM" "$image" ;;
  esac
done

# The first Python that runs: on Windows "python3" is often only the Store
# placeholder, which exits with an error.
PYTHON=""
for candidate in python3 python; do
  if "$candidate" -c 'import sys' >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
done
[ -n "$PYTHON" ] || { echo "python3 is needed to verify the saved images." >&2; exit 1; }
verify_image() { "$PYTHON" -I "$HERE/check-images.py" "$1"; }
# Docker Desktop on Windows wants D:/... in -v, not Git Bash's /d/...
host_path() { if command -v cygpath >/dev/null; then cygpath -m "$1"; else echo "$1"; fi; }

step "Saving images to $OUT/images"
: > "$OUT/IMAGES.txt"
for image in $images; do
  file="$OUT/images/$(echo "$image" | sed -e 's#^docker\.io/##' -e 's#[/:]#_#g').tar"
  printf '%-45s %s\n' "$image" "$(docker image inspect --format '{{.Id}}' "$image")" >> "$OUT/IMAGES.txt"
  if [ "${RESUME:-}" = 1 ] && [ -f "$file" ] && verify_image "$file" >/dev/null; then
    echo "  $image: already saved"
    continue
  fi
  echo "  $image -> $(basename "$file")"
  rm -f "$file"
  # --platform: pulled images are multi-arch indexes; only amd64 is wanted.
  docker save --platform "$PLATFORM" -o "$file" "$image" || true
  if verify_image "$file"; then
    continue
  fi
  # docker save "succeeds" but leaves out layers this machine shares with
  # other images. Images from a registry can be fetched whole from there.
  case "$image" in
    paperless-ngx-custom:*|paperless-ollama:*)
      echo "ERROR: $image could not be saved completely." >&2
      exit 1
      ;;
  esac
  echo "  incomplete; fetching $image from its registry instead"
  rm -f "$file"
  MSYS_NO_PATHCONV=1 docker run --rm -v "$(host_path "$OUT/images"):/out" \
    quay.io/skopeo/stable:latest copy --quiet \
    --override-os linux --override-arch amd64 \
    "docker://$image" "docker-archive:/out/$(basename "$file"):$image"
  verify_image "$file" || { echo "ERROR: $image is still incomplete." >&2; exit 1; }
done

step "Checksums"
(cd "$OUT" && sha256sum images/*.tar > SHA256SUMS)

cat >> "$OUT/IMAGES.txt" <<EOF

Source commit: $REV
Built:         $(date -u +%Y-%m-%dT%H:%M:%SZ)
Platform:      $PLATFORM
AI model:      $([ "${NO_AI:-}" = 1 ] && echo "not bundled" || echo "$AI_MODEL (in $OLLAMA_IMAGE)")
EOF

step "Done: $OUT"
du -sh "$OUT"/images/* "$OUT"
