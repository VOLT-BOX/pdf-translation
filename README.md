# PDF Translation

一个可 Docker 部署的 PDF 翻译服务。项目提供统一的 FastAPI 接口，可以上传 PDF，创建翻译任务，查询任务进度，并下载翻译后的 PDF 或双语对照表。

本文档重点说明如何把项目 clone 下来并直接运行。

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/VOLT-BOX/pdf-translation.git
cd pdf-translation
```

### 2. 准备环境变量

项目不会提交真实密钥。首次运行前，先复制一份 `.env`：

```bash
cp .env.example .env
```

Windows PowerShell 可以使用：

```powershell
Copy-Item .env.example .env
```

然后编辑 `.env`，填写自己的模型接口配置：

```env
LLM_API_KEY=your_api_key
LLM_MODEL=your_model_name
LLM_BASE_URL=https://your-api-base-url/v1
RETAIN_OCR_PROVIDER=cloud
RETAIN_PADDLE_TOKEN=your_paddle_ocr_token
PORT=8040
```

常用配置说明：

| 变量 | 是否必填 | 说明 |
| --- | --- | --- |
| `LLM_API_KEY` | 是 | 大模型 API Key。不要提交到 GitHub。 |
| `LLM_MODEL` | 是 | 翻译使用的模型名称。 |
| `LLM_BASE_URL` | 是 | OpenAI 兼容接口地址。 |
| `RETAIN_OCR_PROVIDER` | 否 | OCR 来源，默认 `cloud`。可选 `cloud`、`local`。 |
| `RETAIN_PADDLE_TOKEN` | 视情况 | `RETAIN_OCR_PROVIDER=cloud` 且处理扫描件/OCR 场景时需要。 |
| `RETAIN_PADDLE_API_URL` | 否 | 云端 PaddleOCR API 地址覆盖项，通常留空。 |
| `RETAIN_LOCAL_OCR_URL` | 视情况 | `RETAIN_OCR_PROVIDER=local` 时使用，默认 `http://host.docker.internal:8080`。 |
| `PORT` | 否 | 服务端口，默认 `8040`。 |
| `WORK_ROOT` | 否 | 容器内工作目录，默认 `/data`。 |
| `RETENTION_SECONDS` | 否 | 结果文件保留时间，默认 `3600` 秒。 |
| `RETAIN_TIMEOUT` | 否 | OCR 翻译子进程超时时间，默认 `3600` 秒。 |

### 3. Docker 启动

在项目根目录执行：

```bash
docker compose up -d --build
```

启动后访问：

```text
http://localhost:8040
```

接口文档地址：

```text
http://localhost:8040/docs
```

如果部署在服务器上，把 `localhost` 换成服务器 IP 或域名即可。

## OCR 来源选择

扫描件、图片型 PDF 和图片翻译会走 OCR。项目支持在 `.env` 中设置默认 OCR，也支持创建任务时临时覆盖。对外只需要理解两个选项：`cloud` 表示云端 PaddleOCR，`local` 表示本地部署 OCR。

### 使用云端 PaddleOCR

`.env` 中保持：

```env
RETAIN_OCR_PROVIDER=cloud
RETAIN_PADDLE_TOKEN=your_paddle_ocr_token
```

`RETAIN_PADDLE_API_URL` 和 `RETAIN_PADDLE_MODEL` 都有默认值，正常不用填。

创建任务时不传 `ocr_provider`，就会使用 `.env` 默认配置。也可以在请求里临时指定：

```bash
curl -X POST http://localhost:8040/tasks \
  -F "file=@example.pdf" \
  -F "lang_in=es" \
  -F "lang_out=zh" \
  -F "text_based=false" \
  -F "ocr_provider=cloud"
```

### 使用本地 PaddleOCR-VL

先单独启动 PaddleOCR-VL 的 PaddleX 服务，例如：

```bash
docker run -d \
  --name paddleocr-vl \
  --gpus all \
  --network host \
  --user root \
  ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddleocr-vl:latest-nvidia-gpu-sm120 \
  bash -lc "paddlex --serve --pipeline PaddleOCR-VL --host 0.0.0.0 --port 8080"
```

确认服务可访问：

```bash
curl http://127.0.0.1:8080/docs
```

`.env` 中改为：

```env
RETAIN_OCR_PROVIDER=local
RETAIN_LOCAL_OCR_URL=http://host.docker.internal:8080
```

本地模式默认调用 PaddleX 的 `/layout-parsing` 接口，返回结果会继续走项目内置的 Paddle adapter，因此可以保留更细的版面、表格和坐标结构。

Linux 服务器 Docker 场景下，`docker-compose.yml` 已配置：

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

如果主服务容器仍访问不到宿主机 PaddleX 服务，可以把 `RETAIN_LOCAL_OCR_URL` 改成宿主机网关 IP，例如：

```env
RETAIN_LOCAL_OCR_URL=http://172.17.0.1:8080
```

也可以单次请求临时选择本地 OCR：

```bash
curl -X POST http://localhost:8040/tasks \
  -F "file=@example.pdf" \
  -F "lang_in=es" \
  -F "lang_out=zh" \
  -F "text_based=false" \
  -F "ocr_provider=local"
```

本地接入默认通过 `local_ocr/paddlex_paddleocr.py` 调用本机 PaddleX 服务。它会把 PDF 以 Base64 发送到 `/layout-parsing`，保存 PaddleOCR-VL 的结构化结果，再交给项目内置 Paddle adapter 转成统一 OCR 格式，后续翻译和渲染流程保持一致。

PaddleOCR-VL 有时会把带文字的截图识别成一个 `image` 大块。项目默认开启 `RETAIN_PADDLE_IMAGE_REOCR=1`，会把这类图片块裁剪成临时单页 PDF 再做一次 OCR；只有二次 OCR 拆出更细的文本或表格块时，才会替换原来的大图片块。这样可以继续复用原坐标回填流程，同时避免把整张截图盖成大白块。需要控制耗时或云端调用次数时，可以设置：

```env
RETAIN_PADDLE_IMAGE_REOCR=0
RETAIN_PADDLE_IMAGE_REOCR_MAX_BLOCKS=6
```

兼容说明：旧参数值 `paddle` 等同于 `cloud`，但新部署建议统一使用 `cloud/local`。

### 对比 OCR 结构化结果

可以用同一份 PDF 分别跑 `cloud` 和 `local` 两个任务，然后对比两个任务目录里的 OCR 结果：

```bash
python3 - <<'PY'
import json
from pathlib import Path

task_ids = ["云端任务ID", "本地任务ID"]
for task_id in task_ids:
    root = Path("data") / task_id / "retain_work" / "ocr"
    normalized = root / "normalized" / "document.v1.json"
    raw = root / "result.json"
    print("\n====", task_id, "====")
    print("raw exists:", raw.exists(), raw)
    print("normalized exists:", normalized.exists(), normalized)
    if not normalized.exists():
        continue
    doc = json.loads(normalized.read_text(encoding="utf-8"))
    pages = doc.get("pages") or []
    blocks = [b for p in pages for b in (p.get("blocks") or [])]
    table_blocks = [
        b for b in blocks
        if "table" in str(b.get("type", "")).lower()
        or "table" in str(b.get("sub_type", "")).lower()
        or "table" in str((b.get("content") or {}).get("kind", "")).lower()
        or "<table" in str(b.get("text", "")).lower()
    ]
    with_bbox = sum(1 for b in blocks if len(b.get("bbox") or []) == 4)
    with_lines = sum(1 for b in blocks if b.get("lines"))
    with_segments = sum(1 for b in blocks if b.get("segments"))
    print("pages:", len(pages))
    print("blocks:", len(blocks))
    print("table_blocks:", len(table_blocks))
    print("blocks_with_bbox:", with_bbox)
    print("blocks_with_lines:", with_lines)
    print("blocks_with_segments:", with_segments)
    for b in table_blocks[:3]:
        print("table sample:", {
            "id": b.get("block_id"),
            "type": b.get("type"),
            "sub_type": b.get("sub_type"),
            "bbox": b.get("bbox"),
            "text_head": str(b.get("text", ""))[:120],
        })
PY
```

如果本地结果只有很少的大块正文，`table_blocks=0`，或者所有文字都在一个整页 `bbox` 里，说明本地 OCR 没有返回和云端一样细的版面、表格、坐标结构。它仍然可以翻译正文，但复杂表格和原坐标回填效果会弱很多。

## 修改部署端口

默认端口是 `8040`。

如果想改成 `8080`，修改 `.env`：

```env
PORT=8080
```

然后重启服务：

```bash
docker compose down
docker compose up -d --build
```

新的访问地址是：

```text
http://localhost:8080/docs
```

端口映射由 `docker-compose.yml` 控制：

```yaml
ports:
  - "${PORT:-8040}:${PORT:-8040}"
```

也就是说，`.env` 里的 `PORT` 会同时决定宿主机端口和容器内服务端口。

## 常用 Docker 命令

查看服务状态：

```bash
docker compose ps
```

查看日志：

```bash
docker compose logs -f
```

停止服务：

```bash
docker compose down
```

重新构建：

```bash
docker compose up -d --build
```

## 接口调用

服务启动后，推荐先打开 Swagger 页面调试：

```text
http://localhost:8040/docs
```

完整流程一般是：

```text
上传 PDF -> 创建翻译任务 -> 查询任务状态 -> 下载翻译结果
```

### 健康检查

```bash
curl http://localhost:8040/health
```

### 创建 PDF 翻译任务

接口：

```text
POST /tasks
```

示例，扫描件或图片型 PDF 默认走 OCR：

```bash
curl -X POST http://localhost:8040/tasks \
  -F "file=@example.pdf" \
  -F "lang_in=en" \
  -F "lang_out=zh" \
  -F "text_based=false"
```

如果 PDF 本身有文字层，希望复用原文字层进行翻译，可以设置 `text_based=true`：

```bash
curl -X POST http://localhost:8040/tasks \
  -F "file=@example.pdf" \
  -F "lang_in=en" \
  -F "lang_out=zh" \
  -F "text_based=true"
```

接口会返回任务 ID，例如：

```json
{
  "task_id": "7c40a92e0dbf4342bc92690ae4c0269f",
  "status": "pending"
}
```

### 查询任务状态

```bash
curl http://localhost:8040/tasks/<task_id>
```

任务状态通常包括：

```text
pending / running / succeeded / failed
```

返回结果里可以看到进度、任务阶段、引擎选择和错误信息。进度字段示例：

```json
{
  "progress": 42.9,
  "translated_pages": 3,
  "total_pages": 7,
  "page_progress": 42.9,
  "stage_text": "正在翻译: 3 / 7 页"
}
```

前端可以每 1-2 秒轮询一次 `GET /tasks/<task_id>`，用 `page_progress` 或 `progress` 展示百分比，用 `translated_pages / total_pages` 展示页数。

### 下载翻译结果

下载纯译文 PDF：

```bash
curl -L "http://localhost:8040/tasks/<task_id>/result?type=mono" -o translated.pdf
```

下载双语 PDF，如果当前任务生成了该文件：

```bash
curl -L "http://localhost:8040/tasks/<task_id>/result?type=dual" -o dual.pdf
```

下载双语对照表 CSV：

```bash
curl -L "http://localhost:8040/tasks/<task_id>/result?type=bilingual" -o bilingual.csv
```

运行中也可以尝试实时获取已生成的双语对照表：

```bash
curl -L "http://localhost:8040/tasks/<task_id>/bilingual" -o bilingual.csv
```

### 删除任务

```bash
curl -X DELETE http://localhost:8040/tasks/<task_id>
```

删除后会清理对应任务的工作目录。

## 图片翻译接口

项目也提供图片翻译接口，会把图片转成 PDF，翻译后再导出为 PNG。

### 同步图片翻译

```bash
curl -X POST http://localhost:8040/images/translate \
  -F "file=@scan.png" \
  -F "lang_in=en" \
  -F "lang_out=zh" \
  -o translated.png
```

### 异步图片翻译

提交任务：

```bash
curl -X POST http://localhost:8040/images/translate/async \
  -F "file=@scan.png" \
  -F "lang_in=en" \
  -F "lang_out=zh"
```

查询状态：

```bash
curl http://localhost:8040/images/translate/<task_id>
```

下载结果：

```bash
curl -L http://localhost:8040/images/translate/<task_id>/result -o translated.png
```

## PDF 标准化接口

如果输入 PDF 页面尺寸不统一，可以先调用标准化接口：

```bash
curl -X POST http://localhost:8040/normalize \
  -F "file=@input.pdf" \
  -o normalized.pdf
```

## 主要参数说明

`POST /tasks` 使用 `multipart/form-data`。

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `file` | 必填 | 待翻译 PDF。 |
| `lang_in` | `en` | 源语言。 |
| `lang_out` | `zh` | 目标语言。 |
| `text_based` | `false` | `true` 表示复用 PDF 文字层；`false` 表示走 OCR 场景。 |
| `concurrency` | `4` | 并发数量。 |
| `openai_api_key` | 空 | 可在请求里临时传入，也可以使用 `.env` 里的 `LLM_API_KEY`。 |
| `openai_model` | 空 | 可在请求里临时指定模型。 |
| `openai_base_url` | 空 | 可在请求里临时指定模型接口地址。 |
| `ocr_provider` | 空 | 可临时指定 OCR 来源：`cloud`、`local`。为空时使用 `.env` 的 `RETAIN_OCR_PROVIDER`。 |
| `paddle_api_url` | 空 | 可临时指定云端 PaddleOCR API 地址。 |
| `paddle_token` | 空 | 可在请求里临时传入 OCR token。 |
| `glossary` | 空 | 可选术语表文件，支持 CSV/XLSX。 |
| `glossary_hard` | `false` | 对命中的术语启用占位符硬约束；只作用于成功匹配的 source。 |
| `callback_url` | 空 | 任务完成后的回调地址。 |
| `enable_table_translation` | `false` | 是否翻译表格内容。 |

通常只需要传 `file`、`lang_in`、`lang_out`、`text_based`。模型密钥和本地 OCR 命令建议统一放在 `.env` 中，不建议通过公网 API 暴露服务器命令配置。

## 术语表格式

术语表至少需要包含：

| 字段 | 说明 |
| --- | --- |
| `source` | 原文术语。 |
| `target` | 目标译法。 |
| `src_lng` | 可选，源语言，如 `en`、`es`、`es-ES`。为空表示通用。 |
| `tgt_lng` | 可选，目标语言，如 `zh`、`zh-CN`。为空表示通用。 |
| `level` | 可选，`preferred` / `canonical` / `preserve`，默认 `preferred`。 |

示例：

```csv
source,target,src_lng,tgt_lng,level
steel,钢,en,zh,preferred
acero,钢,es,zh,preferred
API,API,,zh,preserve
```

系统会按当前任务的 `lang_in` 和 `lang_out` 过滤术语。例如 `lang_in=es&lang_out=zh` 时，`src_lng=en` 的术语不会进入本次任务，`src_lng=es` 和空 `src_lng` 的术语会保留。

`glossary_hard=true` 不是整张术语表 100% 生效。它表示：当前文本中成功匹配到的术语会通过占位符进行更强约束；如果 OCR 识别、断行、复数、重音符号或源语言不一致导致没有匹配到，就不会强制替换。

## 本地数据目录

运行时生成的上传文件、中间文件和翻译结果会写入 `data/`，容器内对应 `/data`。

这些内容可能包含用户上传的文档和翻译结果，不建议提交到 GitHub。当前仓库已经通过 `.gitignore` 忽略了 `.env`、`data/`、PPT、PDF、图片结果、缓存和临时文件。

`fonts/` 已提交到仓库，用于保证别人 clone 后可以直接 Docker 构建。

## 常见问题

### 1. 访问不了服务

先看容器是否启动：

```bash
docker compose ps
```

再看日志：

```bash
docker compose logs -f
```

如果端口被占用，修改 `.env` 中的 `PORT`，然后重启。

### 2. LLM 调用失败

检查 `.env`：

```env
LLM_API_KEY=
LLM_MODEL=
LLM_BASE_URL=
```

确认 API Key、模型名和接口地址都正确，并且部署机器可以访问该接口。

### 3. 扫描件翻译失败

扫描件需要 OCR 能力，确认 `.env` 中配置了：

```env
RETAIN_PADDLE_TOKEN=
```

如果只翻译原生文字层 PDF，可以在创建任务时设置：

```text
text_based=true
```

### 4. Docker 构建失败

确认当前目录是项目根目录，并且字体目录存在：

```bash
ls fonts
```

然后重新构建：

```bash
docker compose build --no-cache
docker compose up -d
```

## 项目处理流程

```text
上传 PDF
  |
  v
文件校验与任务创建
  |
  v
识别文档类型与翻译策略
  |
  +-- 原生 PDF / 复用文字层
  |
  +-- 扫描 PDF / OCR 场景
  |
  v
OCR / 文本提取 / 版面解析
  |
  v
生成中间文件
  |
  v
按页或按块组织翻译请求
  |
  v
调用 LLM API
  |
  v
保存翻译结果
  |
  v
按原坐标回填文字、重建页面
  |
  v
生成结果文件和对照表
```
