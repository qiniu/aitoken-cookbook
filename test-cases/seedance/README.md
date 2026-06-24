# Seedance 视频生成兼容性测试

把接口地址指向被测的 Seedance 兼容服务，运行本脚本即可测试该接口，
并在 `reports/` 下生成报告。

校验目标是 **被测端点的 path、请求体、响应体能完全兼容火山方舟视频生成格式**：

- 创建任务 `POST {API_BASE_URL}/contents/generations/tasks`（JSON 请求，返回 `{id}`）
- 查询任务 `GET {API_BASE_URL}/contents/generations/tasks/{id}`（轮询直到终态）

视频生成是异步流程：创建任务拿到 `id` 后，轮询查询接口直到终态
（`succeeded` / `failed` / `expired` / `cancelled`）。

格式权威来源（火山官方文档）：

- [创建视频生成任务](https://www.volcengine.com/docs/82379/1520757?lang=zh)
- [查询视频生成任务](https://www.volcengine.com/docs/82379/1521309?lang=zh)

## 校验方式

响应体结构用 **JSON Schema（draft 2020-12）** 声明式校验，schema 文件本身即一份
「火山格式契约」，放在 [schemas/](schemas/) 下：

| 文件 | 校验对象 |
|------|------|
| [create_response.schema.json](schemas/create_response.schema.json) | 创建任务成功响应（必含非空 `id`） |
| [query_response.schema.json](schemas/query_response.schema.json) | 查询任务响应（任意状态下的基础结构） |
| [error_response.schema.json](schemas/error_response.schema.json) | 错误响应（`{error:{code,message,...}}`） |

成功态的额外约束（必有 `content.video_url` 与 `usage`）由 `run_tests.py` 在内存中基于
查询 schema `allOf` 组合，不单独建文件。

schema 表达不了的跨字段 / 流程语义，保留为少量命名 check：

| check | 含义 |
|------|------|
| `create_status_200` | 创建任务 HTTP 200 |
| `create_schema` | 创建响应通过 `create_response.schema.json` |
| `query_status_200` | 查询任务 HTTP 200 |
| `query_schema` | 查询响应通过 `query_response.schema.json`（轮询中每次校验） |
| `reached_succeeded` | 轮询终态为 `succeeded`（非成功态 fail 并附 `error`） |
| `succeeded_schema` | 终态响应额外满足成功态约束（必有 `content.video_url` 与 `usage`） |
| `usage_total_equals_completion` | `usage.total_tokens == usage.completion_tokens` |
| `create_error_status` | 创建任务返回 4xx（负向用例） |
| `error_schema` | 错误响应通过 `error_response.schema.json` |

计费字段（`usage`）不写死数值，仅由 schema 约束类型与 `minimum: 1`。

## 用例

用例定义见 [cases.yaml](cases.yaml)，每个 case 通过 `scenario` 字段选择生成场景：

| scenario | content[] 结构 | 所需素材 |
|------|------|------|
| `text_to_video` | `[text]` | 无 |
| `image_to_video` | `[text, image_url(first_frame)]` | 1 图 URL |
| `start_end_to_video` | `[text, image_url(first_frame), image_url(last_frame)]` | 2 图 URL |
| `multimodal_reference` | `[text, reference_image, reference_video, reference_audio]` | 多素材 URL |

外加一个负向用例：用不存在的模型 ID 触发错误响应，校验错误格式兼容性。

### 输入素材

图生 / 首尾帧 / 多模态参考场景需要输入图 / 视频 / 音频，用**公网 URL**提供，集中配置在
`cases.yaml` 顶部，默认填火山官方文档示例素材（`ark-project.tos-cn-beijing.volces.com/...`）。

> 被测服务需能访问这些公网 URL。如环境访问不到，请在 `cases.yaml` 顶部替换为你自己的素材 URL。

## 依赖

```bash
pip install pyyaml jsonschema
```

HTTP 请求使用标准库 `urllib`，无需安装 requests。相比 gpt-image-2 套件，新增 `jsonschema`
依赖用于响应体结构校验。

## 运行

把地址指向被测服务，填入密钥，然后运行：

```bash
export API_BASE_URL="https://your-domain.com/api/v3"   # 被测接口地址
export API_KEY="your-api-key"                          # 被测接口密钥
export SEEDANCE_MODEL="doubao-seedance-2-0-260128"     # 被测模型 id（也可用 --model 覆盖）
python run_tests.py
```

所有 case 默认**并发**执行（视频生成较慢，串行会很耗时），各 case 内部独立轮询，
报告顺序仍与 `cases.yaml` 定义一致。

环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `API_BASE_URL` | 无（必填） | 被测接口的基础地址 |
| `API_KEY` | 无（必填） | 被测接口的鉴权密钥 |
| `SEEDANCE_MODEL` | `doubao-seedance-2-0-260128` | 被测模型 id（也可用 `--model` 覆盖） |

被测模型 id 可自定义，命令行参数优先于环境变量：

```bash
python run_tests.py --model your-model-name
```

仅创建 + 单次查询、不等待终态（快速冒烟，省时省钱；此时不要让 case 声明 `reached_succeeded`）：

```bash
python run_tests.py --no-poll
```

不打真实接口、仅自测「请求体构造与 schema 加载」（无需配置地址和密钥）：

```bash
python run_tests.py --dry-run
```

轮询间隔与超时在 `cases.yaml` 顶部配置（`poll_interval` / `poll_timeout`，单位秒）。

## 结果

运行后在 `reports/` 下生成 `report.json` / `report.md` / `report.html`
三份报告，格式见 [test-cases 总览](../README.md#结果格式)。
进程退出码：全部通过为 0，否则为 1。

每个 case 的 `details` 会完整记录本次请求与响应，便于失败定位：

- `scenario` / `model`：场景与被测模型
- `create_url` / `create_body`：创建任务的请求 URL 与请求体
- `task_id` / `polls` / `task_status`：任务 ID、轮询次数、最终任务状态
- `create_response` / `query_response`：完整响应体（超长字符串已截断，仅保留前 500 字符）
- `usage`：计费返回

`query_response.content.video_url` 命中 HTML 报告的视频媒体提示词，会自动内嵌视频预览。

提交结果时打包 `reports/` 下的三份报告；如有 case 失败，
请一并附上失败 case 的 ID 与 `error` 信息。
