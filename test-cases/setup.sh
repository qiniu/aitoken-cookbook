#!/usr/bin/env bash
#
# 一键创建 test-cases 共享的 Python 虚拟环境并安装依赖。
#
# 在 test-cases/ 下创建 .venv（已被根 .gitignore 忽略），并按 requirements.txt
# 安装所有模型测试脚本共用的第三方依赖。各模型目录下的 run_tests.py 共用此环境。
#
# 用法：
#   bash test-cases/setup.sh          # 创建 .venv（已存在则复用）并安装依赖
#   bash test-cases/setup.sh -f       # 强制删除并重建 .venv
#
set -euo pipefail

# 定位到脚本自身所在目录（test-cases/），保证在任意路径下运行都正确
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
REQUIREMENTS="${SCRIPT_DIR}/requirements.txt"

# 解析参数：-f / --force 表示重建
FORCE=0
for arg in "$@"; do
  case "$arg" in
    -f | --force) FORCE=1 ;;
    *)
      echo "error: unknown argument '$arg' (supported: -f, --force)" >&2
      exit 2
      ;;
  esac
done

# 选取 python 解释器，优先 python3
if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "error: python3 not found, please install Python 3 first" >&2
  exit 1
fi

# 检查 requirements.txt 是否存在
if [ ! -f "$REQUIREMENTS" ]; then
  echo "error: requirements.txt not found at ${REQUIREMENTS}" >&2
  exit 1
fi

# 强制模式：先删除旧环境
if [ "$FORCE" -eq 1 ] && [ -d "$VENV_DIR" ]; then
  echo "==> removing existing venv: ${VENV_DIR}"
  rm -rf "$VENV_DIR"
fi

# 创建虚拟环境（已存在则复用）
if [ -d "$VENV_DIR" ]; then
  echo "==> reusing existing venv: ${VENV_DIR}"
else
  echo "==> creating venv: ${VENV_DIR}"
  "$PYTHON" -m venv "$VENV_DIR"
fi

# 在虚拟环境内升级 pip 并安装依赖
VENV_PY="${VENV_DIR}/bin/python"
echo "==> upgrading pip"
"$VENV_PY" -m pip install --upgrade pip >/dev/null
echo "==> installing dependencies from requirements.txt"
"$VENV_PY" -m pip install -r "$REQUIREMENTS"

echo ""
echo "Done. Activate the virtual environment with:"
echo "  source ${VENV_DIR}/bin/activate"
echo ""
echo "Then run a model test suite, e.g.:"
echo "  cd ${SCRIPT_DIR}/seedance && python run_tests.py"
