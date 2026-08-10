# Seedance 视频生成兼容性测试

把接口地址指向被测的 Seedance 兼容服务，运行本脚本即可测试该接口，
并在 `reports/` 下生成报告。

校验目标是 **被测端点的 path、请求体、响应体能完全兼容火山方舟视频生成格式**：

- 创建任务 `POST {API_BASE_URL}/contents/generations/tasks`（JSON 请求，返回 `{id}`）
- 查询任务 `GET {API_BASE_URL}/contents/generations/tasks/{id}`（轮询直到终态）

视频生成是异步流程：创建任务拿到 `id` 后，轮询查询接口直到终态
（`succeeded` / `failed` / `expired` / `cancelled`）。

格式权威来源（火山官方文档）：

- [创建视频生成任务](https://docs.volcengine.com/docs/82379/1520757?lang=zh)
- [查询视频生成任务](https://docs.volcengine.com/docs/82379/1521309?lang=zh)
- [Doubao Seedance 2.5 使用教程](https://docs.volcengine.com/docs/82379/2607688?lang=zh)
- [Doubao Seedance 2.0 系列使用教程](https://docs.volcengine.com/docs/82379/2291680?lang=zh)

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
| `succeeded_has_last_frame` | 开启 `return_last_frame:true` 后，成功响应含 `content.last_frame_url`（尾帧图 URL） |
| `create_error_status` | 创建任务返回 4xx（负向用例） |
| `error_schema` | 错误响应通过 `error_response.schema.json` |
| `error_code_matches` | `error.code` 精确等于 case 声明的 `expected_error_code`（负向用例） |
| `status_queued_or_running` | 首次查询 `status` 为 `queued` 或 `running`（进行中态）；判的是创建后第一次查询的响应，故可与 `reached_succeeded` 同时声明，无需为进行中态单独建任务 |
| `query_resolution_matches_request` | 查询响应的 `resolution` 与创建请求一致（用于 4K） |
| `query_duration_matches_request` | 查询响应的 `duration` 与创建请求一致（用于 30 秒） |
| `succeeded_video_format_matches_request` | 对生成 URL 发 Range 请求，确认 MOV 产物的 `ftyp` major brand 为 `qt  ` |

计费字段（`usage`）不写死数值，仅由 schema 约束类型与 `minimum: 1`。

### 软校验（`warn_checks`）

除硬校验（`checks`，不通过即 fail）外，case 可声明 `warn_checks` 软校验：**不满足只记录警告、不影响 pass/fail**，用于「火山原生格式」这类兼容性提示（被测服务可能有意改写，不算错误）。警告会出现在报告的 `warnings` 列与摘要「警告 N」计数中。

| warn_check | 含义 |
|------|------|
| `id_volc_format` | 创建与查询响应的 `id` 均为火山格式（`cgt-` + 14 位时间戳 + `-` + 随机串，如 `cgt-20260420145835-68j7n`）；否则提示自行生成了非火山格式 ID（如 UUID） |
| `video_url_is_volc` | 成功响应 `content.video_url` 为火山链接（host 以 `volces.com` 结尾）；否则提示视频被转存到自有 CDN、未透传火山原始链接 |

## 用例

用例定义见 [cases.yaml](cases.yaml)，每个 case 通过 `scenario` 字段选择生成场景：

| scenario | content[] 结构 | 所需素材 |
|------|------|------|
| `text_to_video` | `[text]` | 无 |
| `image_to_video` | `[text, image_url(first_frame)]` | 1 图 URL |
| `reference_to_video` | `[text, image_url(reference_image), ...]` | 1 参考图 URL；case 声明 `reference_image_urls`（列表）可传多张 |
| `start_end_to_video` | `[text, image_url(first_frame), image_url(last_frame)]` | 2 图 URL |
| `multimodal_reference` | `[text, reference_image, reference_video, reference_audio]` | 多素材 URL |
| `audio_only_reference` | `[text, reference_audio]` | 1 音频 URL |
| `video_edit` / `video_extend` | `[text, reference_video]` | 1 视频 URL |
| `reference_images_profile_max` | `[text, reference_image × profile 上限, reference_video, reference_audio]` | 9/30 图片项 + 1 视频 + 1 音频 |
| `multimodal_reference_6_videos` | `[text, reference_image, reference_video × 6]` | 1 图片 + 6 视频 URL |

### Profile 与能力用例

`--model` 只写入请求体，可以是官方 Model ID、Endpoint ID 或自定义模型别名；测试程序不会从字符串推断模型版本。必填的 `--profile` 独立声明被测模型的能力：

| Profile | 分辨率 | 最长时长 | 输出格式 | 纯音频参考 | 图片/视频/音频/总素材上限 |
|------|------|------:|------|------|------|
| `seedance-2.5` | 480p、720p | 30 秒 | mp4、mov | 支持 | 30 / 10 / 10 / 50 |
| `seedance-2.0` | 480p、720p、1080p、4k | 15 秒 | mp4 | 不支持 | 9 / 3 / 3 / 15 |
| `seedance-2.0-fast` | 480p、720p | 15 秒 | mp4 | 不支持 | 9 / 3 / 3 / 15 |
| `seedance-2.0-mini` | 480p、720p | 15 秒 | mp4 | 不支持 | 9 / 3 / 3 / 15 |

所有能力用例默认包含在测试集合中。不满足当前 profile 的用例不会发请求，而是以 `skipped` 记录具体原因；`skipped` 不影响整体 PASS/FAIL。

| 用例 | 执行 profile | 验证内容 |
|------|------|------|
| `t2v_full` | `seedance-2.5` | 单次请求同时验证 30 秒、MOV、返回尾帧、进行中/成功态与计费字段 |
| `t2v_full` | `seedance-2.0` | 单次请求同时验证 4K、返回尾帧、进行中/成功态与计费字段 |
| `t2v_full` | Fast / Mini | 单次 720p、5 秒请求验证通用文生全流程 |
| `audio_only_reference` | `seedance-2.5` | 仅文本和参考音频即可成功 |
| `reference_images_profile_max` | 全部 | 单次请求合并图片上限与多模态：2.5 为 30 图 + 1 视频 + 1 音频，2.0 系列为 9 图 + 1 视频 + 1 音频 |
| `multimodal_reference_6_videos` | `seedance-2.5` | 官方 1 图 + 6 视频参考成功 |

视频编辑和视频延长在所有 profile 执行。2.5 会自动应用官方限制：首帧/首尾帧使用 `ratio: adaptive`，视频编辑使用 `ratio: adaptive` 与 `duration: -1`，视频延长使用 `ratio: adaptive`。

文生视频主用例 `t2v_full` 对每个 profile 只生成一次视频，并通过 profile 覆盖合并能力参数：2.5 请求 30 秒 MOV，2.0 标准版请求 4K，Fast / Mini 请求 720p、5 秒。该请求同时承载全部可复用的文生校验：

- **进行中态**（`status_queued_or_running`）：判**首次轮询响应**——创建后第一次查询天然处于 `queued` / `running`，因此不必为「进行中态」单独建任务。
- **尾帧透传**（`return_last_frame: true` + `succeeded_has_last_frame`）：尾帧只是个请求参数，校验成功响应在 `content.last_frame_url` 返回尾帧图（用于识别不透传该参数的实现），挂在本用例上不额外生成视频。
- **火山原生格式软校验**（`id_volc_format` + `video_url_is_volc`）：复用已生成的视频，提示任务 id 形如 `cgt-20260420145835-68j7n`、`content.video_url` 的 host 以 `volces.com` 结尾。不满足只记警告（提示自行生成非火山格式 ID 或把视频转存到自有 CDN），不判失败。详见下文[软校验](#软校验warn_checks)。

按默认用例执行时，Seedance 2.5 预计真正出片 9 次（30 秒文生 1 次、5 秒任务 7 次、`duration: -1` 视频编辑 1 次）；Seedance 2.0 标准版、Fast 和 Mini 各预计出片 7 次，均为 5 秒任务。另有 3 个创建阶段应失败的负向请求，不会出片。

外加一个多图参考正向用例：参考图传两个**火山官方公开素材**的 `asset://` 引用（`asset-20260224190652-n8sd2` 洛丽塔连衣裙、`asset-20260401123823-6d4x2` 中国 26 岁女性网红），prompt 让「图2的女生穿上图1的裙子」，多图参考合成并轮询到成功。与下方无效素材负向用例互为正反面，验证兼容火山方舟的实现能正确解析有效 `asset://` 引用并生成视频。

外加三个负向用例：

- 用不存在的模型 ID 触发错误响应，校验错误格式兼容性。
- 首尾帧传含**真人**的图（火山官方真人示例图）创建视频，兼容火山方舟的实现应做真人 / 隐私检测并拒绝，返回 4xx 且 `error.code` 精确为 `InputImageSensitiveContentDetected.PrivacyInformation`（用于暴露未透传真人图片检测错误的被测实现）。
- 参考图用不存在的 `asset://` 素材引用创建视频，兼容火山方舟的实现应校验素材引用并拒绝，返回 4xx 且 `error.code` 精确为 `InvalidParameter`（用于暴露未校验 asset 引用或返回别的错误码的被测实现）。

> `prompt` / `first_frame_url` / `last_frame_url` 等素材字段支持在单个 case 中覆盖全局配置，
> 负向用例可声明 `expected_error_code` 供 `error_code_matches` 校验精确错误码。

单个 case 可通过 `poll: once` 声明仅查询一次（不轮询到终态），与全局 `--no-poll` 等效但只作用于该 case。

### 输入素材

图生 / 首尾帧 / 多模态参考场景的素材全部通过**公网 URL**提供，不使用 Base64 或本地媒体 fixture。默认值与新增能力用例均采用火山官方文档中的 `ark-project.tos-cn-beijing.volces.com` 或 `arkdocs.tos-cn-beijing.volces.com` 示例链接。

六视频用例使用 Seedance 2.5 教程中的 6 段视频，时长约为 5.062、5.085、3.553、5.085、4.267、5.085 秒，总计约 28.14 秒。当前官方可用示例无法在 30 秒总时长限制内合法覆盖 10 段视频、10 段音频或 50 个总素材，因此本套件不声称覆盖这三个上限；profile 仍记录官方能力值供本地请求数量校验。

> 被测服务需能访问这些公网 URL。如环境访问不到，请在 `cases.yaml` 顶部替换为你自己的素材 URL。

## 依赖

```bash
pip install pyyaml jsonschema
```

HTTP 请求使用标准库 `urllib`，无需安装 requests。相比 gpt-image-2 套件，新增 `jsonschema`
依赖用于响应体结构校验。

## 运行

进入 Seedance 测试目录，把地址和密钥指向被测服务：

```bash
cd test-cases/seedance
export API_BASE_URL="https://your-domain.com/api/v3"
export API_KEY="your-api-key"
```

执行命令需要明确指定能力档案 `--profile`。`--model` 是实际写入请求体的模型标识，可传官方 Model ID、Endpoint ID 或自定义模型别名：

```bash
python run_tests.py \
  --model doubao-seedance-2-5-xxxxxx \
  --profile seedance-2.5
```

两者互相独立，程序不会根据 `--model` 字符串推断能力。使用 Endpoint ID 或自定义别名时同样显式声明 profile：

```bash
python run_tests.py \
  --model ep-custom-seedance-prod \
  --profile seedance-2.5
```

可用 profile：

| 被测模型 | 参数 |
|------|------|
| Seedance 2.5 | `--profile seedance-2.5` |
| Seedance 2.0 标准版 | `--profile seedance-2.0` |
| Seedance 2.0 Fast | `--profile seedance-2.0-fast` |
| Seedance 2.0 Mini | `--profile seedance-2.0-mini` |

能力用例默认全部参与本次测试：profile 支持的用例会执行，不支持的用例自动记为 `skipped`，无需额外开启扩展测试参数。

所有 case 默认**并发**执行（视频生成较慢，串行会很耗时），各 case 内部独立轮询，
报告顺序仍与 `cases.yaml` 定义一致。

环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `API_BASE_URL` | 无（必填） | 被测接口的基础地址 |
| `API_KEY` | 无（必填） | 被测接口的鉴权密钥 |
| `SEEDANCE_MODEL` | `doubao-seedance-2-0-260128` | 被测模型标识（Model ID、Endpoint ID 或自定义别名；`--model` 优先） |

也可以用环境变量设置模型标识；`--profile` 仍必须通过命令行传入：

```bash
export SEEDANCE_MODEL="ep-custom-seedance-prod"
python run_tests.py --profile seedance-2.5
```

仅创建 + 单次查询、不等待终态（快速冒烟，省时省钱；此时不要让 case 声明 `reached_succeeded`）：

```bash
python run_tests.py \
  --model ep-custom-seedance-prod \
  --profile seedance-2.0-mini \
  --no-poll
```

也可在单个 case 上声明 `poll: once`，与其它需轮询到终态的 case 混跑。

不打真实接口、仅自测「请求体构造与 schema 加载」（无需配置地址和密钥）：

```bash
python run_tests.py \
  --model ep-custom-seedance-prod \
  --profile seedance-2.5 \
  --dry-run
```

轮询间隔与超时在 `cases.yaml` 顶部配置（`poll_interval` / `poll_timeout`，单位秒）。

## 结果

运行后在 `reports/` 下生成 `report.json` / `report.md` / `report.html`
三份报告，格式见 [test-cases 总览](../README.md#结果格式)。
进程退出码：执行的用例全部通过为 0，否则为 1（`skipped` 与软校验警告均不影响退出码）。

`warn_checks` 产生的软校验警告记录在每个 case 的 `warnings` 列，摘要行以「警告 N」计数，HTML 报告中带警告的通过用例以淡黄底标记。

每个 case 的 `details` 会完整记录本次请求与响应，便于失败定位：

- `scenario` / `model` / `profile`：场景、请求中的模型标识与能力档案
- `skip_reason`：用例因 profile 不支持而跳过时的具体原因
- `create_url` / `create_body`：创建任务的请求 URL 与请求体
- `task_id` / `polls` / `task_status`：任务 ID、轮询次数、最终任务状态
- `first_task_status`：首次查询到的任务状态（`status_queued_or_running` 据此校验）
- `create_response` / `query_response`：完整响应体（超长字符串已截断，仅保留前 500 字符）
- `usage`：计费返回

`query_response.content.video_url` 命中 HTML 报告的视频媒体提示词，会自动内嵌视频预览。

提交结果时打包 `reports/` 下的三份报告；如有 case 失败，
请一并附上失败 case 的 ID 与 `error` 信息。
