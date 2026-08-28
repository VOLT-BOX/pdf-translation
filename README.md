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
RETAIN_PADDLE_TOKEN=your_paddle_ocr_token
PORT=8040
```

常用配置说明：

| 变量 | 是否必填 | 说明 |
| --- | --- | --- |
| `LLM_API_KEY` | 是 | 大模型 API Key。不要提交到 GitHub。 |
| `LLM_MODEL` | 是 | 翻译使用的模型名称。 |
| `LLM_BASE_URL` | 是 | OpenAI 兼容接口地址。 |
| `RETAIN_PADDLE_TOKEN` | 视情况 | 扫描件/OCR 场景需要。只翻译带文字层的 PDF 时可不填。 |
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

返回结果里可以看到进度、任务阶段、引擎选择和错误信息。

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
| `paddle_token` | 空 | 可在请求里临时传入 OCR token。 |
| `glossary` | 空 | 可选术语表文件。 |
| `callback_url` | 空 | 任务完成后的回调地址。 |
| `enable_table_translation` | `false` | 是否翻译表格内容。 |

通常只需要传 `file`、`lang_in`、`lang_out`、`text_based`。模型密钥建议统一放在 `.env` 中。

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
