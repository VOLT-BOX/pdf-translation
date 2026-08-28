# 统一 PDF 翻译服务

把 **v3(babeldoc)** 与 **RetainPDF** 合并成**一个 FastAPI 进程**:整本单引擎路由,
默认整本走 RetainPDF(OCR),`text_based=true` 则整本走 v3(复用文字层,版面保持好)。
对外一套 `/tasks` 接口,无需再起三个容器。

## 为什么合并

原架构是三个独立服务,router 通过 HTTP 调 v3(:8001)和 RetainPDF(:8020):
- 多容器/多端口/多 compose,部署重;router↔引擎间 HTTP 跳变多余。
- 轮询开销、超时对齐麻烦(v3 的 TASK_TIMEOUT 曾误杀快完成的任务)。

合并后:一个进程、一个端口(8030)、一个 compose。v3 直调(无 HTTP),RetainPDF 保留
subprocess(隔离其 `apply_layout_tuning` 进程全局态,这是必须的,不是冗余)。

## 两条引擎的调用方式

| | v3 (babeldoc) | RetainPDF |
|---|---|---|
| 调用 | **进程内直调** `v3_worker.run_translate` | **subprocess** `python run_job.py spec.json` |
| 为何如此 | `run_translate` 自包含、无全局态冲突 | `apply_layout_tuning` 是进程全局态,并发会互相踩,subprocess 隔离 |

## 架构

```
客户端 POST /tasks (PDF + 超集参数)
  │
  ▼
统一服务(FastAPI, :8030)
  1. 整本单引擎路由(`text_based` 参数):
     - `text_based=false`(默认)→ 整本走 RetainPDF(OCR)
     - `text_based=true`       → 整本走 v3(复用文字层)
     不做逐页检测——逐页切分会在 v3↔OCR 引擎边界处把跨页断句割断,整本统一才是正确解。
  2. 决策:retainpdf / v3(不再有 mixed)
  3. 编排:
     - v3:     进程内直调 run_translate(同步,进度回调)
     - retain: subprocess 跑 run_job.py(同步,跑完取产物)
  4. 合并:按原页序拼 mono/dual PDF + concat bilingual CSV
  │
  ▼
  v3_worker.run_translate      retain.run_retain (subprocess)
  (babeldoc,进程内)            (pipeline + typst,子进程)
```

## 目录结构

| 文件 | 作用 | 来源 |
|---|---|---|
| `app.py` | FastAPI 路由 + 任务存储 + worker 队列 + /normalize | 新写(合并 router + normalize) |
| `orchestrate.py` | 编排:v3 直调 + RetainPDF subprocess,无 httpx | 新写 |
| `classify.py` | 整本单引擎路由 + 切片 + manifest | 改自 router/(去掉逐页图片覆盖率判定) |
| `merge.py` | 按页序合并 + CSV concat | 拷自 router/(零改) |
| `config.py` | 合并配置(LLM/paddle/typst/阈值) | 新写 |
| `v3_worker.py` | babeldoc 翻译(解耦版) | 改自 v3/service/worker.py |
| `v3_models.py` | TranslateParams/V3Record | 改自 v3/service/models.py |
| `retain.py` | RetainPDF spec 构造 + subprocess + 产物分类 | 改自 RetainPDF app.py |
| `normalize.py` | PDF 标准化到 A4(函数) | 改自 normalize_pdf_service.py |
| `babeldoc/` | babeldoc 引擎源码(vendored) | 原样拷自 v3/babeldoc/ |
| `pipeline/` | RetainPDF 官方 pipeline 源码 | 原样拷自 RetainPDF/.../pipeline/ |
| `run_job.py` | RetainPDF 子进程入口 | 原样拷 |
| `fonts/` `fontconfig/` | 思源宋体 + 字体别名 | 原样拷 |
| `pyproject.toml` | babeldoc 可编辑安装声明 | 拷自 v3/ |
| `Dockerfile` `entrypoint.sh` `docker-compose.yml` | 部署 | 新写/合并 |

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/tasks` | 上传 PDF + 超集参数,返回 task_id(202) |
| GET | `/tasks` | 列出所有任务 |
| GET | `/tasks/{id}` | 查状态/进度/决策 |
| GET | `/tasks/{id}/result?type=mono\|dual\|bilingual` | 下载合并产物 |
| GET | `/tasks/{id}/bilingual` | 实时下载已翻译的原文↔译文对照表 CSV(运行中可用) |
| DELETE | `/tasks/{id}` | 删任务及工作目录 |
| POST | `/normalize` | PDF 页面标准化到 A4 |
| POST | `/images/translate` | 图片翻译(同步):图→PDF→RetainPDF(OCR)→译文图(同尺寸 PNG) |
| POST | `/images/translate/async` | 图片翻译(异步):提交返回 task_id,后台翻译 |
| GET | `/images/translate/{id}` | 查图片翻译状态 |
| GET | `/images/translate/{id}/result` | 下载译文图(同尺寸 PNG) |
| GET | `/health` | 健康检查 |
| GET | `/docs` | Swagger UI |

任务状态:`pending` → `running` → `succeeded` / `failed`

### 创建任务参数(POST /tasks,multipart/form-data)

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `file` | File | 必填 | 待翻译 PDF(原生/扫描/混合均可) |
| `lang_in` | str | `en` | 源语言 |
| `lang_out` | str | `zh` | 目标语言 |
| `concurrency` | int | `4` | v3 作 qps(上限 20)、RetainPDF 作 workers |
| `openai_api_key` | str | 无 | LLM key;缺则服务端 `LLM_API_KEY` 兜底(两引擎共用) |
| `openai_model` | str | 无 | 模型名(留空用服务端默认) |
| `openai_base_url` | str | 无 | OpenAI 兼容服务地址 |
| `paddle_token` | str | 无 | RetainPDF 用(扫描页);缺则服务端 env 兜底 |
| `mode` | str | `fast` | RetainPDF 翻译模式:fast/precise/sci |
| `no_mono` | bool | `false` | v3 用:不输出单语 PDF |
| `no_watermark` | bool | `true` | v3 用:去水印 |
| `glossary` | File | 无 | 可选术语表 CSV/XLSX(表头含 source、target) |
| `glossary_hard` | bool | `false` | 术语硬约束 |
| `custom_system_prompt` | str | 无 | 自定义译者系统提示 |
| `callback_url` | str | 无 | 翻译完成后 POST 通知此 URL |
| `text_based` | bool | `false` | 文本型 PDF(有文字层)填 true 复用文字层直译;扫描件/图片型 PDF 填 false 走 OCR |
| `enable_table_translation` | bool | `false` | RetainPDF 扫描页:是否翻译表格(默认 False=跳过表格) |

返回 202(回显关键输入,脱敏不含 `openai_api_key`):

```json
{
  "task_id": "7c40a92e0dbf4342bc92690ae4c0269f",
  "status": "pending",
  "original_filename": "mixed.pdf",
  "created_at": "2026-08-13T...",
  "progress": 0.0,
  "stage": "",
  "decision": "",
  "result_files": [],
  "error": null,
  "lang_in": "en",
  "lang_out": "zh",
  "concurrency": 4,
  "custom_system_prompt": null,
  "glossary_hard": false
}
```

`GET /tasks/{id}` 同上,分类后 `decision` 取值 `v3` / `retainpdf`(不再有 `mixed`)。

### 下载结果

```bash
curl -o merged.pdf    "http://<host>:8030/tasks/<id>/result?type=mono"
curl -o dual.pdf      "http://<host>:8030/tasks/<id>/result?type=dual"
curl -o bilingual.csv "http://<host>:8030/tasks/<id>/result?type=bilingual"
```

- `mono`:必有,按原页序合并的纯译文 PDF。
- `dual`:best-effort(整本单引擎下,双引擎页数 1:1 才合并)。
- `bilingual`:原文↔译文对照表 CSV(单引擎产物直通)。

### 图片翻译(POST /images/translate)

单张图片 → 内部转 PDF → RetainPDF(OCR)翻译 → 转回**与原图同尺寸**的 PNG,同步返回图片字节。

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `file` | File | 必填 | 单张图片(png/jpg/webp 等) |
| `lang_in` | str | `en` | 源语言 |
| `lang_out` | str | `zh` | 目标语言 |
| `openai_api_key` | str | 无 | LLM key;缺则服务端 `LLM_API_KEY` 兜底 |
| `openai_model` | str | 无 | 模型名(留空用默认) |
| `openai_base_url` | str | 无 | OpenAI 兼容服务地址 |
| `paddle_token` | str | 无 | PaddleOCR token;缺则服务端 env 兜底 |
| `mode` | str | `fast` | RetainPDF 翻译模式:fast/precise/sci |
| `custom_system_prompt` | str | 无 | 自定义译者系统提示 |
| `enable_table_translation` | bool | `false` | 图片含表格时是否翻译表格 |

尺寸还原原理:图转 PDF 时**等比缩放、不留白**(长边压到 ≤842pt,避免像素当 point 的巨纸夹死字号),译文 PDF 再按同一比例光栅化回原始像素,故宽高比与像素尺寸精确一致。响应头 `X-Image-Size` 返回 `w x h`(px)。

```bash
curl -X POST http://<host>:8030/images/translate \
  -F "file=@scan.png" -F "lang_out=zh" \
  -F "openai_api_key=sk-xxx" -F "paddle_token=xxx" \
  -o translated.png
# → 响应头 X-Image-Size: 1200x800(与输入一致)
```

### 图片翻译(异步,POST /images/translate/async)

先提交、后下载,适合大图/批量/需要进度查询的场景;参数与同步版一致。

```bash
# 1) 提交(返回 task_id,202)
curl -X POST http://<host>:8030/images/translate/async \
  -F "file=@scan.png" -F "lang_out=zh" \
  -F "openai_api_key=sk-xxx" -F "paddle_token=xxx"
# → {"task_id":"7c40...","status":"pending","kind":"image","image_size":"1200x800",...}

# 2) 轮询状态(pending → running → succeeded)
curl http://<host>:8030/images/translate/7c40...
# → {"status":"succeeded","image_size":"1200x800","result_files":["scan.translated.png"],...}

# 3) 下载译文图(与原图同尺寸 PNG)
curl -o translated.png "http://<host>:8030/images/translate/7c40.../result"
```

与 `/tasks` 共用同一套任务存储:`GET /tasks` 里也会列出图片任务(`kind=image`),
`DELETE /tasks/{id}` 可清理。

## 部署

### Docker

```bash
cd unified
# 配凭证(复制 .env.example 为 .env 并填值,或直接 export)
docker compose up -d --build
```

健康检查:
```bash
curl http://127.0.0.1:8030/health   # {"status":"ok","work_root":"/data"}
```

### 配置(环境变量)

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_API_KEY` | (空) | LLM key(v3 与 RetainPDF 共用),必填 |
| `LLM_MODEL` | `deepseek-v4-flash` | 默认模型 |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容地址 |
| `RETAIN_PADDLE_TOKEN` | (空) | PaddleOCR token(RetainPDF 扫描页用) |
| `ALLOW_RETRANSLATE` | `true` | 允许重复翻译(禁用 babeldoc 标记检查) |
| `MIN_TEXT_CHARS` | `10` | 已废弃(整本单引擎路由不再逐页判定) |
| `IMAGE_COVER_THRESHOLD` | `0.45` | 已废弃(不再逐页判图片覆盖率) |
| `IMAGE_DOMINANT_TEXT_CHARS` | `300` | 已废弃(不再逐页判定) |
| `RETAIN_TIMEOUT` | `3600` | RetainPDF 子进程超时秒 |
| `PORT` | `8030` | 服务端口 |
| `WORK_ROOT` | `/data` | 工作目录 |
| `RETENTION_SECONDS` | `3600` | 完成任务保留秒 |

镜像 bake 的 Typst/字体 env(`TYPST_BIN`/`RETAIN_PDF_*FONT*` 等)由 Dockerfile 设好,无需手动配。

### 本地开发(不用 Docker)

```bash
cd unified
python -m venv .venv && .venv\Scripts\activate
pip install -e . -r requirements.txt
set LLM_API_KEY=sk-xxx
python app.py
```

## 调用示例

```bash
# 提交(默认整本走 RetainPDF/OCR)
curl -X POST http://127.0.0.1:8030/tasks \
  -F "file=@scan.pdf" -F "lang_out=zh" \
  -F "openai_api_key=sk-xxx" -F "paddle_token=xxx"
# → {"task_id":"7c40...","status":"pending",...}

# 整本走 v3(复用文字层)
curl -X POST http://127.0.0.1:8030/tasks \
  -F "file=@native.pdf" -F "lang_out=zh" -F "text_based=true" \
  -F "openai_api_key=sk-xxx"

# 轮询(看 decision)
curl http://127.0.0.1:8030/tasks/7c40...
# → {"status":"succeeded","decision":"retainpdf",...}

# 下载合并译文
curl -o merged.pdf "http://127.0.0.1:8030/tasks/7c40.../result?type=mono"

# 图片转 PDF 先标准化再翻译
curl -X POST http://127.0.0.1:8030/normalize -F "file=@images.pdf" -o normalized.pdf
```

## 数据与排查

- 任务数据在 `./data/<task_id>/`(`input.pdf`、切片、`v3_work/`、`retain_work/`、`output/` 合并结果)。
- v3 产物在 `<task>/v3_work/output/`,RetainPDF 产物在 `<task>/retain_work/rendered/`。
- 排查:`GET /tasks/{id}` 看 `decision`/`error`;RetainPDF 子任务 stderr 在 `retain_work/` 下,
  也可看 `retain_work/spec.json`。
- 内存存储,重启丢失。要持久化/扩容换 Redis。

## 已知限制

- 已去掉混合件模式:整本单引擎路由(默认 OCR,`text_based=true` 走 v3),避免引擎边界处的跨页断句割裂。
  `merge.py` / `split_pdf` 的混合件兜底代码仍保留(dormant),不再被 `text_based` 路由触发。
- RetainPDF subprocess 仍需 typst + 预览包 + 字体,镜像较大(2-3GB,含 onnx 模型)。
- 串行 router worker(一次一个 PDF)。要扩容仍需多容器。
- hyperscan 在 arm64 需构建链;先按 x86_64。
