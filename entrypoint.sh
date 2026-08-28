#!/usr/bin/env sh
set -eu

# /data 是挂载卷,构建期写的内容会被空卷覆盖,因此运行时再把预置的
# Typst preview 包(cmarker / mitex)铺到缓存目录,让 Typst 编译走本地命中。
CACHE="${TYPST_PACKAGE_CACHE_PATH:-/data/typst-package-cache}"
SRC="${TYPST_PACKAGE_PATH:-/opt/typst-packages}"

if [ -d "$SRC/preview" ]; then
  for pkg in cmarker/0.1.8 mitex/0.2.6; do
    target="$CACHE/preview/$pkg"
    source="$SRC/preview/$pkg"
    if [ ! -d "$target" ] && [ -d "$source" ]; then
      mkdir -p "$(dirname "$target")"
      cp -R "$source" "$target"
    fi
  done
fi

mkdir -p "$CACHE" /data

exec "$@"
