# 无 Docker 部署指南（pip / 物理机 · 内网环境）

适用版本：pdf-translation f250b60（README 的部署方式为 Docker，本文档为纯 Python 环境替代方案）。

---

## 0. 前置条件

| 项 | 要求 |
|---|---|
| 操作系统 | Linux x86_64（Ubuntu / CentOS / 统信UOS / 麒麟等均可） |
| Python | **3.10 – 3.13**（建议 3.11，与 Docker 镜像一致） |
| 内存 | ≥ 8 GB（整本翻译时 babeldoc 解析 + 渲染峰值较高） |
| 磁盘 | ≥ 10 GB 空闲（pip 包 + 模型/字体缓存 + 任务工作目录） |
| 工作目录 | 需可写（默认 `./data`，可用 `WORK_ROOT` 改到数据盘） |

不需要 Docker、不需要 GPU。OCR 走云端 PaddleOCR 或内网 OCR 服务，本机只做解析/渲染。

---

## 1. 方案选择（内网服务器怎么选）

| 场景 | 方案 |
|---|---|
| 服务器完全无公网 | **离线资源包方案**（见 4d）：公网机器执行 `babeldoc --generate-offline-assets` 打包全部模型/字体/分词器缓存 → U盘/内网拷贝 → 服务器执行 `babeldoc --restore-offline-assets`。运行期零外网请求 |
| 服务器可出公网 | 在线方案：装好后执行 `babeldoc --warmup` 预下载（见 4d） |
| pip 安装受限 | 公网机器 `pip download` 全量包 → 拷入内网 `pip install --no-index --find-links`（见 3.2） |

运行期 LLM 翻译与 OCR 需要的外部地址见第 5 节，请提前在网络策略中放行，或按第 7 节替换为内网自建端点。

---

## 2. 获取代码

```bash
git clone https://github.com/VOLT-BOX/pdf-translation.git
cd pdf-translation
```

内网机器拿不到 GitHub 时，在公网机器打包后拷入：

```bash
tar czf pdf-translation-src.tar.gz --exclude=.git pdf-translation/
# 内网解压即可，源码自带 vendored babeldoc/（无需从 PyPI 安装 BabelDOC）
```

获取源码这一步需要一次公网（仅此一次）；之后的依赖安装、模型下载全部可离线化。

---

## 3. Python 依赖安装

### 3.1 在线安装（服务器可出公网时）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
# 国内服务器建议走清华源
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt -e .
```

说明：

- `-r requirements.txt`：服务壳 + RetainPDF pipeline（fastapi、uvicorn、PyMuPDF、Pillow、pikepdf、hyperscan、httpx 等）
- `-e .`：把仓库内 vendored 的 babeldoc/ 以可编辑方式装入环境（其依赖声明在 `pyproject.toml`，pip 会自动解析）
- 这两条等价于 Docker 镜像里的 `uv pip install -r pyproject.toml` + `-r requirements.txt` + `-e /app`
- **hyperscan**：无预编译 wheel 的平台会在安装时报错或运行时降级为逐条正则匹配。若 `pip install` 因 hyperscan 失败，可先注释掉 requirements.txt 中的 hyperscan 行完成安装 —— 服务会自动走正则回退（功能不变，术语匹配速度变慢）
- `opencv-python-headless`、`onnxruntime` 等 wheel 较大，离线打包时注意体积

### 3.2 离线安装（服务器无公网时）

在公网机器上（操作系统架构与 Python 版本需与目标服务器一致）：

```bash
cd pdf-translation
pip download -d wheels/ -r requirements.txt
pip wheel . -w wheels/          # 把 vendored babeldoc 连同其依赖打成 wheel
tar czf offline-pkg.tar.gz wheels/
# 连同源码一起拷入内网
```

内网服务器上：

```bash
cd pdf-translation
python3 -m venv .venv && source .venv/bin/activate
pip install --no-index --find-links ../wheels/ -r requirements.txt
pip install --no-index --find-links ../wheels/ wheels/babeldoc-*.whl
```

---

## 4. Typst 与字体（无 Docker 时需手动准备）

Docker 镜像预置了 Typst 二进制、Typst 预览包、思源宋体和 fontconfig 别名；pip 部署按下述步骤自行准备。**所有下载均可在公网机器完成后拷入内网。**

### 4.1 Typst 渲染引擎（必须）

```bash
cd /tmp
curl -fsSL -o typst.tar.xz \
  https://github.com/typst/typst/releases/download/v0.14.2/typst-x86_64-unknown-linux-musl.tar.xz
tar -xJf typst.tar.xz
sudo cp typst-x86_64-unknown-linux-musl/typst /usr/local/bin/typst
typst --version    # 应输出 0.14.2
```

### 4.2 Typst 预览包 cmarker 0.1.8 / mitex 0.2.6（公式渲染）

```bash
sudo mkdir -p /opt/typst-packages/preview/cmarker/0.1.8
sudo mkdir -p /opt/typst-packages/preview/mitex/0.2.6

curl -fsSL -o cmarker.tar.gz https://packages.typst.org/preview/cmarker-0.1.8.tar.gz
mkdir /tmp/cmarker && tar -xzf cmarker.tar.gz -C /tmp/cmarker
sudo cp -R /tmp/cmarker/. /opt/typst-packages/preview/cmarker/0.1.8/

curl -fsSL -o mitex.tar.gz https://packages.typst.org/preview/mitex-0.2.6.tar.gz
mkdir /tmp/mitex && tar -xzf mitex.tar.gz -C /tmp/mitex
sudo cp -R /tmp/mitex/. /opt/typst-packages/preview/mitex/0.2.6/
```

目录结构（`TYPST_PACKAGE_PATH` 指向 `/opt/typst-packages`）：

```
/opt/typst-packages/preview/cmarker/0.1.8/
/opt/typst-packages/preview/mitex/0.2.6/
```

Typst 编译时本地命中优先，不再访问 packages.typst.org。

### 4.3 中文字体

思源宋体（正文/标题渲染用）已在仓库 `fonts/` 目录，**无需下载**：

```
fonts/SourceHanSerifSC-Regular.otf
fonts/SourceHanSerifSC-Bold.otf
```

直接通过环境变量指向仓库目录即可（推荐，省去安装步骤）：

```bash
export RETAIN_PDF_FONT_PATH=/opt/pdf-translation/fonts/SourceHanSerifSC-Regular.otf
export RETAIN_PDF_TITLE_BOLD_FONT_PATH=/opt/pdf-translation/fonts/SourceHanSerifSC-Bold.otf
export RETAIN_PDF_TYPST_FONT_DIRS=/opt/pdf-translation/fonts
```

（把 `/opt/pdf-translation` 换成实际部署路径。）

Docker 镜像额外装了 `fonts-noto-cjk`（系统级 CJK 兜底字体）。扫描件走 RetainPDF 管线时建议同样安装：

```bash
# Ubuntu / Debian
sudo apt-get install -y fonts-noto-cjk
# CentOS / openEuler（包名因发行版而异）
sudo yum install -y google-noto-sans-cjk-ttc-fonts google-noto-serif-cjk-ttc-fonts
```

离线时把 `.ttc/.otf` 拷到 `~/.local/share/fonts/`，然后 `fc-cache -f`；或把该目录追加进 `RETAIN_PDF_TYPST_FONT_DIRS`（冒号分隔可多个）。

> Docker 镜像里的 `fontconfig/65-source-han-serif-alias.conf` 只是字体别名配置。pip 环境直接用 `RETAIN_PDF_TYPST_FONT_DIRS` 喂字体目录给 Typst，不装 fontconfig 也能正常渲染。

### 4.4 环境变量汇总（本节产物）

```bash
export TYPST_BIN=/usr/local/bin/typst
export TYPST_PACKAGE_PATH=/opt/typst-packages
export TYPST_PACKAGE_CACHE_PATH=/opt/pdf-translation/data/typst-package-cache   # 任意可写目录
export RETAIN_PDF_FONT_PATH=/opt/pdf-translation/fonts/SourceHanSerifSC-Regular.otf
export RETAIN_PDF_TITLE_BOLD_FONT_PATH=/opt/pdf-translation/fonts/SourceHanSerifSC-Bold.otf
export RETAIN_PDF_TYPST_FONT_DIRS=/opt/pdf-translation/fonts
```

---

## 4d. babeldoc 模型/字体资源（内网部署核心）

babeldoc（v3 文字层管线）首次运行会自动下载版面分析模型、字体元数据等资源，默认来源：

| 资源 | 来源域名 | 说明 |
|---|---|---|
| DocLayout-YOLO ONNX 模型 | huggingface.co（可切 hf-mirror.com / modelscope.cn） | 版面分析，v3 管线必用 |
| font_metadata.json + 40 余个回退字体 | raw.githubusercontent.com（可切 huggingface.co / modelscope.cn） | 原字体缺失时的渲染回退 |
| CMap 文件 | 同上 | CJK 编码映射 |
| tiktoken 缓存 | openaipublic.blob.core.windows.net | LLM token 计数 |

**离线方案（内网服务器推荐）** —— 在一台装好同版本 babeldoc 的公网机器上：

```bash
babeldoc --generate-offline-assets /path/to/output_dir
# 生成 offline_assets_<tag>.zip，拷入内网服务器后：
babeldoc --restore-offline-assets /path/to/offline_assets_<tag>.zip
```

恢复后 babeldoc 全部资源走本地缓存，**运行期零外网依赖**。

在线机器直接执行：

```bash
babeldoc --warmup
```

国内服务器在线下载慢时，可切换下载源（可选）：

```bash
export HF_ENDPOINT=https://hf-mirror.com     # huggingface 走国内镜像
```

---

## 5. 公网依赖地址清单（网络放行单）

### 安装期一次性下载（全部可离线化，见上文）

| 用途 | 域名 | 端口 | 离线替代 |
|---|---|---|---|
| pip 包 | pypi.org / files.pythonhosted.org（或清华 tuna） | 443 | ✅ pip download |
| 源码 | github.com（本项目仓库为 VOLT-BOX 私有仓库） | 443 | ✅ tar 包拷入 |
| Typst 二进制 | github.com + objects.githubusercontent.com（Releases 附件） | 443 | ✅ 预下载拷入 |
| Typst 预览包 | packages.typst.org | 443 | ✅ 预下载拷入 |
| babeldoc 模型/字体 | huggingface.co、hf-mirror.com、modelscope.cn、raw.githubusercontent.com、openaipublic.blob.core.windows.net | 443 | ✅ offline-assets 包 |

### 运行期持续依赖（按配置必放行，否则对应功能不可用）

| 用途 | 域名 | 端口 | 备注 |
|---|---|---|---|
| LLM 翻译（v3 与 RetainPDF 共用） | `.env` 中 `LLM_BASE_URL` 配置 | 443 | 默认 `https://api.deepseek.com/v1`；可用任意 OpenAI 兼容端点替换（见第 7 节） |
| 云端 PaddleOCR（扫描页 OCR） | `paddleocr.aistudio-app.com` | 443 | 仅 `RETAIN_OCR_PROVIDER=cloud` 且有扫描件时需要；本地 OCR 模式不需要 |

版面分析（docvision）默认使用本地 ONNX 推理（CPU 即可），远程 RPC 端点需显式配置才会启用，默认无此请求。

**除以上两处外运行期无其他外网请求。** 装好后 `babeldoc --warmup` 成功 + 服务 `/health` 返回 ok，即证明资源全部本地化。

---

## 6. 配置与启动

### 6.1 重要：无 Docker 时 .env 不会自动加载

Docker compose 用 `env_file` 把 `.env` 灌进容器；pip 环境没有这一步，**必须显式导出**（`config.py` 只认环境变量，不读 .env 文件）：

```bash
cp .env.example .env
# 编辑 .env：至少填 LLM_API_KEY / LLM_MODEL / LLM_BASE_URL
# 无 Docker 注意：RETAIN_LOCAL_OCR_URL 的 host.docker.internal 改成实际内网 IP

# 在 .env 末尾追加（无 Docker 必设）：
#   TYPST_BIN=/usr/local/bin/typst
#   TYPST_PACKAGE_PATH=/opt/typst-packages
#   TYPST_PACKAGE_CACHE_PATH=/opt/pdf-translation/data/typst-package-cache
#   RETAIN_PDF_FONT_PATH=/opt/pdf-translation/fonts/SourceHanSerifSC-Regular.otf
#   RETAIN_PDF_TITLE_BOLD_FONT_PATH=/opt/pdf-translation/fonts/SourceHanSerifSC-Bold.otf
#   RETAIN_PDF_TYPST_FONT_DIRS=/opt/pdf-translation/fonts

set -a; source .env; set +a
python app.py
```

### 6.2 验证

```bash
curl http://127.0.0.1:8040/health        # {"status":"ok",...}
# 浏览器打开 http://<服务器IP>:8040/docs 可看 Swagger UI
```

上传一本文字型 PDF（`text_based=true`）+ 一页扫描件分别验证 v3 与 RetainPDF 两条管线。

### 6.3 systemd 开机自启（可选）

推荐只用 `EnvironmentFile` 一行，全部变量写在 .env 里（含 6.1 追加的六项），避免 `Environment=` 逐条拼写错误：

```ini
# /etc/systemd/system/pdf-translate.service
[Unit]
Description=Unified PDF Translation Service
After=network.target

[Service]
User=appuser
WorkingDirectory=/opt/pdf-translation
EnvironmentFile=/opt/pdf-translation/.env
ExecStart=/opt/pdf-translation/.venv/bin/python app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pdf-translate
```

注意：`.env` 中含 `LLM_API_KEY` 等敏感信息，`chmod 600 .env` 并仅限运行用户可读。

---

## 7. 内网自建端点替换（纯内网运行）

| 项 | 替换方式 |
|---|---|
| LLM 翻译 | `.env`: `LLM_BASE_URL=http://<内网vLLM等>:8000/v1` + `LLM_MODEL` + `LLM_API_KEY`（任意 OpenAI 兼容服务均可） |
| 扫描页 OCR | `.env`: `RETAIN_OCR_PROVIDER=local` + `RETAIN_LOCAL_OCR_URL=http://<内网OCR>:8080`（本地 PaddleX `/layout-parsing` 服务） |
| 版面分析 | babeldoc docvision 默认即本地 ONNX 推理，无需配置 |

三处都替换 + 4d 离线资源包恢复后，**整个服务可运行于纯内网，运行期外网依赖为 0**。

---

## 8. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 启动即报 `typst: command not found` 或渲染失败 | `TYPST_BIN` 未设置或 Typst 未安装（4.1） |
| 公式/表格渲染报 `package not found` | `TYPST_PACKAGE_PATH` 未指向含 cmarker/mitex 的目录（4.2） |
| 中文显示为方块/乱码 | 字体环境变量未设置（4.3），检查 `RETAIN_PDF_TYPST_FONT_DIRS` |
| 首次翻译卡在"下载模型" | 内网无法访问 huggingface —— 用 4d 离线资源包方案 |
| hyperscan 安装失败 | requirements.txt 注释掉该行，服务自动降级正则匹配（3.1） |
| 扫描件翻译报 OCR 错误 | `RETAIN_OCR_PROVIDER` 与凭证/URL 配置不匹配，或云端域名未放行（第 5 节） |
