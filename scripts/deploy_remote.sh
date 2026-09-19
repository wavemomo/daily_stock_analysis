#!/usr/bin/env bash
# ===================================================================
# 一键部署后端到远程服务器
# ===================================================================
# 流程：本地构建 linux/amd64 镜像 -> 流式传输到远端 docker load
#      -> 同步源码树（保留服务器侧 .env 与 compose 改动）
#      -> 打回滚标签 -> compose 重建容器 -> 健康检查
#
# 用法:
#   scripts/deploy_remote.sh                  # 完整部署
#   scripts/deploy_remote.sh --skip-tests     # 跳过本地测试门禁
#   scripts/deploy_remote.sh --dry-run        # 只做预检与计划，不改动远端
#   scripts/deploy_remote.sh --rollback       # 回滚到最近一个 rollback-* 标签
#   scripts/deploy_remote.sh --rollback TAG   # 回滚到指定标签
#   scripts/deploy_remote.sh --list           # 列出远端可用镜像标签
#
# 可用环境变量覆盖默认值:
#   REMOTE_HOST   默认 root@124.221.191.229
#   REMOTE_DIR    默认 /opt/daily-stock-analysis
#   IMAGE_NAME    默认 daily-stock-analysis-server（必须与 compose 推导名一致）
#   PLATFORM      默认 linux/amd64（远端为 x86_64；本机 Apple Silicon 需跨架构构建）
#   API_PORT      默认 8000（仅用于健康检查地址）
#   KEEP_IMAGES   默认 6（远端保留的历史镜像标签数量）
#   HEALTH_TIMEOUT 默认 180（秒）
# ===================================================================
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-root@124.221.191.229}"
REMOTE_DIR="${REMOTE_DIR:-/opt/daily-stock-analysis}"
IMAGE_NAME="${IMAGE_NAME:-daily-stock-analysis-server}"
PLATFORM="${PLATFORM:-linux/amd64}"
API_PORT="${API_PORT:-8000}"
KEEP_IMAGES="${KEEP_IMAGES:-6}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"
COMPOSE_FILE="docker/docker-compose.yml"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SKIP_TESTS=0
DRY_RUN=0
MODE="deploy"
ROLLBACK_TAG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-tests) SKIP_TESTS=1; shift ;;
    --dry-run)    DRY_RUN=1; shift ;;
    --list)       MODE="list"; shift ;;
    --rollback)
      MODE="rollback"; shift
      if [[ $# -gt 0 && "$1" != --* ]]; then ROLLBACK_TAG="$1"; shift; fi
      ;;
    -h|--help)    sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "未知参数: $1（--help 查看用法）" >&2; exit 2 ;;
  esac
done

log()  { printf '\033[1;34m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

# ssh 复用连接，避免每步重新握手
SSH_CTL="/tmp/dsa-deploy-ssh-%r@%h:%p"
SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=$SSH_CTL" -o ControlPersist=120
          -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
rsh() { ssh "${SSH_OPTS[@]}" "$REMOTE_HOST" "$@"; }

# -------------------------------------------------------------------
# 预检
# -------------------------------------------------------------------
log "预检本地环境"
command -v docker >/dev/null || die "未找到 docker"
docker buildx version >/dev/null 2>&1 || die "未找到 docker buildx（跨架构构建必需）"
docker info >/dev/null 2>&1 || die "docker daemon 未运行"
[[ -f "$COMPOSE_FILE" ]] || die "缺少 $COMPOSE_FILE，请在仓库根目录运行"

log "预检远端 $REMOTE_HOST"
rsh "test -d '$REMOTE_DIR'" || die "远端目录不存在: $REMOTE_DIR"
rsh "command -v docker >/dev/null" || die "远端未安装 docker"
rsh "docker compose version >/dev/null 2>&1" || die "远端缺少 docker compose 插件"
rsh "test -f '$REMOTE_DIR/.env'" || die "远端缺少 $REMOTE_DIR/.env（不会由本脚本创建）"
REMOTE_ARCH="$(rsh 'uname -m')"
log "远端架构: $REMOTE_ARCH / 构建平台: $PLATFORM"

# -------------------------------------------------------------------
# --list / --rollback
# -------------------------------------------------------------------
remote_tags() {
  rsh "docker images '$IMAGE_NAME' --format '{{.Tag}}\t{{.CreatedAt}}' | sort -k2 -r"
}

if [[ "$MODE" == "list" ]]; then
  log "远端 $IMAGE_NAME 可用标签："
  remote_tags
  exit 0
fi

if [[ "$MODE" == "rollback" ]]; then
  if [[ -z "$ROLLBACK_TAG" ]]; then
    ROLLBACK_TAG="$(rsh "docker images '$IMAGE_NAME' --format '{{.Tag}}\t{{.CreatedAt}}' \
      | grep -E '^rollback-' | sort -k2 -r | head -1 | cut -f1")"
    [[ -n "$ROLLBACK_TAG" ]] || die "远端没有 rollback-* 标签可回滚，请用 --list 选择标签"
  fi
  log "回滚到 $IMAGE_NAME:$ROLLBACK_TAG"
  [[ "$DRY_RUN" == 1 ]] && { log "dry-run：跳过实际回滚"; exit 0; }
  rsh "set -e
    cd '$REMOTE_DIR'
    docker image inspect '$IMAGE_NAME:$ROLLBACK_TAG' >/dev/null
    docker tag '$IMAGE_NAME:$ROLLBACK_TAG' '$IMAGE_NAME:latest'
    docker compose -f '$COMPOSE_FILE' up -d --no-build --force-recreate server"
  log "回滚完成，开始健康检查"
  MODE="healthcheck-only"
fi

# -------------------------------------------------------------------
# 本地测试门禁
# -------------------------------------------------------------------
if [[ "$MODE" == "deploy" ]]; then
  if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
    warn "工作树有未提交改动，构建出的镜像将无法由某个提交精确复现"
  fi

  if [[ "$SKIP_TESTS" == 0 ]]; then
    log "运行本地测试门禁（--skip-tests 可跳过）"
    if ! python3 -m pytest -m "not network" -q >/tmp/dsa-deploy-tests.log 2>&1; then
      tail -20 /tmp/dsa-deploy-tests.log
      warn "测试未全绿，详见 /tmp/dsa-deploy-tests.log"
      read -r -p "仍要继续部署？输入 yes 继续: " reply
      [[ "$reply" == "yes" ]] || die "已按要求中止部署"
    else
      log "测试门禁通过"
    fi
  else
    warn "已跳过测试门禁"
  fi
fi

# -------------------------------------------------------------------
# 构建 + 传输 + 切换
# -------------------------------------------------------------------
if [[ "$MODE" == "deploy" ]]; then
  STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  GIT_REV="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  NEW_TAG="deploy-${STAMP}-${GIT_REV}"
  WEB_VERSION="$(node -p "require('./apps/dsa-web/package.json').version" 2>/dev/null || echo 0.0.0)"

  log "目标镜像: $IMAGE_NAME:$NEW_TAG (HEAD=$GIT_REV, web=$WEB_VERSION)"

  if [[ "$DRY_RUN" == 1 ]]; then
    log "dry-run：以下步骤将被执行但现在跳过"
    printf '  1) docker buildx build --platform %s -t %s:%s\n' "$PLATFORM" "$IMAGE_NAME" "$NEW_TAG"
    printf '  2) docker save | gzip | ssh %s docker load\n' "$REMOTE_HOST"
    printf '  3) rsync 源码到 %s（排除 .env / compose / data / logs / reports）\n' "$REMOTE_DIR"
    printf '  4) 远端把当前 latest 打成 rollback-%s 后切换并重建容器\n' "$STAMP"
    printf '  5) 健康检查 http://127.0.0.1:%s/api/health\n' "$API_PORT"
    exit 0
  fi

  log "步骤 1/5 构建镜像（跨架构构建耗时较长）"
  docker buildx build \
    --platform "$PLATFORM" \
    -f docker/Dockerfile \
    --build-arg "DSA_WEB_VERSION=$WEB_VERSION" \
    --build-arg "DSA_WEB_REVISION=$GIT_REV" \
    -t "$IMAGE_NAME:$NEW_TAG" \
    --load \
    .

  log "步骤 2/5 传输镜像到远端（流式，不落本地临时文件）"
  docker save "$IMAGE_NAME:$NEW_TAG" | gzip -1 \
    | rsh "gunzip | docker load"
  rsh "docker image inspect '$IMAGE_NAME:$NEW_TAG' >/dev/null" \
    || die "远端未找到刚上传的镜像 $IMAGE_NAME:$NEW_TAG"

  log "步骤 3/5 同步源码树（保留服务器侧 .env 与 compose 改动）"
  # strategies/ 会以 ro 方式挂载进容器，必须同步；
  # .env 与 docker-compose.yml 在服务器上有本机化改动（端口仅绑 127.0.0.1），绝不覆盖。
  rsync -az --delete \
    -e "ssh ${SSH_OPTS[*]}" \
    --exclude '.git/' \
    --exclude '.env' \
    --exclude '.env.*' \
    --exclude 'docker/docker-compose.yml' \
    --exclude 'docker/docker-compose.yml.*' \
    --exclude 'data/' \
    --exclude 'logs/' \
    --exclude 'reports/' \
    --exclude 'longbridge_tokens/' \
    --exclude 'static/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'node_modules/' \
    --exclude '.pytest_cache/' \
    --exclude '.venv/' \
    ./ "$REMOTE_HOST:$REMOTE_DIR/"

  log "步骤 4/5 打回滚标签并切换容器"
  rsh "set -e
    cd '$REMOTE_DIR'
    if docker image inspect '$IMAGE_NAME:latest' >/dev/null 2>&1; then
      docker tag '$IMAGE_NAME:latest' '$IMAGE_NAME:rollback-${STAMP}'
      echo '[remote] 已保存回滚点 $IMAGE_NAME:rollback-${STAMP}'
    fi
    docker tag '$IMAGE_NAME:$NEW_TAG' '$IMAGE_NAME:latest'
    docker compose -f '$COMPOSE_FILE' up -d --no-build --force-recreate server"
fi

# -------------------------------------------------------------------
# 健康检查
# -------------------------------------------------------------------
log "步骤 5/5 健康检查（最多 ${HEALTH_TIMEOUT}s）"
if ! rsh "deadline=\$(( \$(date +%s) + $HEALTH_TIMEOUT ))
  while [ \$(date +%s) -lt \$deadline ]; do
    status=\$(docker inspect stock-server --format '{{.State.Health.Status}}' 2>/dev/null || echo missing)
    if [ \"\$status\" = healthy ]; then echo '[remote] 容器健康检查通过'; exit 0; fi
    if [ \"\$status\" = missing ]; then echo '[remote] 容器不存在'; exit 1; fi
    sleep 5
  done
  echo '[remote] 健康检查超时，最后状态: '\$status; exit 1"; then
  warn "健康检查未通过，最近日志："
  rsh "docker logs --tail 40 stock-server 2>&1" || true
  die "部署后服务未达健康状态。可执行 scripts/deploy_remote.sh --rollback 回滚"
fi

log "接口连通性验证"
rsh "curl -fsS -m 10 'http://127.0.0.1:${API_PORT}/api/health' | head -c 300; echo" \
  || warn "健康接口直连失败（容器已 healthy，可能是端口或路由差异）"

# -------------------------------------------------------------------
# 清理旧镜像
# -------------------------------------------------------------------
log "清理远端历史镜像（保留最近 $KEEP_IMAGES 个）"
rsh "docker images '$IMAGE_NAME' --format '{{.Tag}}\t{{.CreatedAt}}' \
  | grep -vE '^(latest)\b' | sort -k2 -r | tail -n +$((KEEP_IMAGES + 1)) | cut -f1 \
  | while read -r tag; do
      [ -n \"\$tag\" ] || continue
      docker rmi '$IMAGE_NAME':\"\$tag\" >/dev/null 2>&1 && echo \"[remote] 已删除 $IMAGE_NAME:\$tag\" || true
    done" || warn "镜像清理未完全成功（不影响本次部署）"

log "完成。远端当前镜像："
rsh "docker ps --filter name=stock-server --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'"
log "如需回滚：scripts/deploy_remote.sh --rollback"
