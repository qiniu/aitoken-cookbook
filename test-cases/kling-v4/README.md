# Kling 4.0 视频生成能力测试

沿用 Seedance / MiniMax 的 YAML 用例、异步执行、响应 Schema 和公共报告结构，使用可灵官方新版协议，通过 `API_BASE_URL` 切换官方服务或兼容网关。运行仅依赖仓库共享的 Python 依赖，无需额外安装媒体工具。

## 用例范围

共 **7 个用例：5 个正向生成场景 + 2 个负向参数用例**，规模与 Seedance、MiniMax 接近。正向预期生成5段视频；负向应在创建阶段被拒绝，不进入轮询。分辨率、时长、比例、默认值和水印参数合并到对应请求中；自动校验接口响应与返回时长，实际媒体参数和画面效果通过人工验收。

| 用例 ID | 能力 | 合并验证 |
|---|---|---|
| `t2v_full` | 文生视频 | 30秒上界、1080p、21:9、分段叙事、水印 |
| `start_end_frames` | 首尾帧 | 3秒下界、720p、首帧Base64与尾帧URL、画面过渡 |
| `multi_keyframes` | 多关键帧 | 首/中/尾三张不同图片、默认10秒与1080p |
| `omni_reference` | 多模态参考及数量上限 | 10张不同参考图 + 5段不同参考视频，合计15项素材；720p、1:1、多素材融合 |
| `omni_edit` | 视频编辑 | 待编辑视频 + 参考图 + 特征参考视频、1080p、adaptive、箱子改色与参考光线/色彩融合、原有运动保留 |

| 负向用例 ID | 参数规则 | 预期 |
|---|---|---|
| `missing_prompt_error` | 文生请求缺少必填 `prompt` | HTTP 400 + 参数错误码 |
| `invalid_duration_error` | 图生请求 `duration=31`，超过30秒 | HTTP 400 + 参数错误码 |

每个负向请求仅违反一条规则，避免不同错误互相遮蔽。参数错误码限定为 `1200` / `1201`；鉴权、欠费、限流、内容审核、服务器错误或HTML响应均不算通过。非法请求若意外被接受，会判失败并记录任务 ID，不继续轮询；这种情况下服务仍可能生成视频。

本套件不测主体库、音色参考与长提示词。默认素材来自可灵官方示例及现有 Seedance 套件的公开示例，不需要额外账户资源 ID。多模态参考使用文本、图片和视频。

参考数量上限直接合并到 `omni_reference`，预期应成功生成，不增加生成任务。无视频角色主体时，官方限制为图片最多10张、视频最多5段、素材总数最多15项；待编辑视频 `base_video` 也占视频数量，且最多1段。每段输入视频与视频总时长均须为3～30秒。

默认上限素材为10张不同图片、5段不同视频，已读取公开媒体头核对尺寸、时长和帧率，视频总时长约25.025秒；编辑用例的待编辑视频约12.042秒、参考视频约7.042秒，合计约19.084秒。这些是输入时长；输出时长仍由各用例的 `settings.duration` 指定。上限用例验证恰好达到数量上限时可用，不包含数量超限请求。

## 官方依据与协议

数量与时长限制核对日期：2026-11-05，依据官方文档及其动态参数定义。

- [4.0 文生视频](https://klingai.com/document-api/api/video/4-0/text-to-video)
- [4.0 图生视频](https://klingai.com/document-api/api/video/4-0/image-to-video)
- [4.0 全能参考生视频](https://klingai.com/document-api/api/video/4-0/video-omni)
- [4.0 提示词指南及公开素材](https://klingai.com/document-api/api/usage-guide/kling-4-0-prompt-guide)
- [错误码](https://klingai.com/document-api/api/get-started/error-codes)
- 用户提供的 [3.0 文生](https://klingai.com/document-api/api/video/3-0-omni/text-to-video)、[3.0 图生](https://klingai.com/document-api/api/video/3-0-omni/image-to-video)用于版本对照，生成参数采用4.0文档。

| 操作 | 请求 |
|---|---|
| 文生 | `POST /text-to-video/kling-4.0`，请求体含 `prompt` / `settings` / `options` |
| 首尾帧、多关键帧 | `POST /image-to-video/kling-4.0`，请求体含 `contents` / `settings` / `options` |
| 多模态参考、编辑 | `POST /omni-video/kling-4.0`，请求体含 `contents` / `settings` / `options` |
| 查询 | `GET /tasks?task_ids=...` |

使用 `Authorization: Bearer {API_KEY}`。创建读取 `data.id`，查询读取 `data` 数组，终态为 `succeeded` / `failed`。`--model` 或 `KLING_MODEL` 覆盖 URL 中的模型路径段，默认 `kling-4.0`。

## 运行

先在仓库根目录安装共享依赖并离线预演；预演不会访问接口或下载Base64素材。

```bash
bash test-cases/setup.sh
source test-cases/.venv/bin/activate
python test-cases/kling-v4/run_tests.py --list
python test-cases/kling-v4/run_tests.py --dry-run
```

设置地址与密钥后直接运行，默认执行全部7个用例并严格校验，全部通过才返回成功。官方地址填写服务根路径；默认最多2个并发任务，单任务轮询超时1800秒。创建请求失败时不会自动重试，以免重复生成。

```bash
export API_BASE_URL="https://api-beijing.klingai.com"
export API_KEY="your-api-key"
python test-cases/kling-v4/run_tests.py
```

兼容网关可使用自己的路由前缀和模型别名：

```bash
export API_BASE_URL="https://your-gateway.example/kling"
python test-cases/kling-v4/run_tests.py --model my-kling-v4
```

需要只运行部分用例时，使用 `--case` 按ID或通配符选择，支持重复指定；所选用例仍须全部通过。

```bash
python test-cases/kling-v4/run_tests.py --case start_end_frames
python test-cases/kling-v4/run_tests.py --case 'omni_*' --poll-timeout 2400
python test-cases/kling-v4/run_tests.py --case '*_error'
```

替换素材时，复制并编辑 [fixtures.example.yaml](fixtures.example.yaml)，通过 `--fixtures /tmp/kling-v4-fixtures.yaml` 指定。保留需要覆盖的键即可。Base64首帧可填写本地路径，其他素材应使用服务端可下载的URL。

上限用例使用 `limit_reference_images` 的前10张图片与 `limit_reference_videos` 的前5段视频，数量不足时在提交前报错，不自动缩减请求。替换时须提供足量、合规的素材，尤其注意5段视频合计不超过30秒；编辑用例使用独立的 `edit_video`、`reference_video`，两者总时长同样不能超过30秒。离线预演只检查配置与请求构造，不下载或重新探测输入媒体。

默认在提交任何任务前检查全部所选场景的素材配置；存在缺失时整次执行报错退出，离线预演同样检查。`--no-poll` 创建后仅查询一次，尚未完成的任务标记为 `skipped`，退出码为1；此模式仍会生成视频，已完成任务仍检查产物。

## 校验与报告

响应体检测复用 [schemas/responses.schema.json](schemas/responses.schema.json)，在原有场景中执行，不另创建视频：

| 阶段 | 检查内容 |
|---|---|
| 创建成功 | HTTP 200、`code=0`、`message`、非空 `request_id`、`data.id/status/create_time/update_time` 的结构与类型 |
| 每次轮询 | 成功信封、`data` 为单任务数组、状态枚举、任务ID与自定义ID匹配、状态与时间不倒退 |
| 生成成功 | 成功态 Schema 要求 `status=succeeded`，且至少包含一项完整视频产物；逐项验证 `type/id/url/duration`、HTTP(S)链接、响应时长与水印链接 |
| 计费与用量 | 字段如有返回，检查余额/资源包结构、数值格式及非负金额；不硬编码价格或其他模型的计费公式 |
| 参数错误 | 同时校验 HTTP 400、错误 Schema、非零整型 `code`、非空 `message/request_id`，以及业务码属于1200/1201；错误响应不能含已创建任务ID |

轮询到 `failed`、超时或响应格式错误都会失败；服务返回HTTP 200但业务码非零也不会通过。只有接口返回预期参数错误时，负向用例才通过，客户端自身异常不能替代服务端校验。

自动检测基于 HTTP 响应，返回的 `duration` 与预期时长允许0.1秒偏差。当前可灵响应契约未要求回显分辨率、画幅比例与帧率，这些参数保留在请求中用于覆盖，不作为已通过的自动检测项。视频及水印链接检查地址格式，不下载输出视频或读取实际媒体流。

实际分辨率、画幅比例、时长、Omni的24fps、可播放性，以及画面遵循、关键帧过渡、参考融合和编辑效果，通过用例的 `review` 清单人工验收。接口通过不等于实际媒体参数或画面质量已通过，报告将人工验收状态保留为 `pending`。

默认输出 `reports/report.json`、`reports/report.md`、`reports/report.html`，全量运行记录7个用例；可用 `--out` 更换目录。内容包含正/负向分类、预期与实际结果、请求、轮询历史、最终响应、计费、视频链接和人工检查项，密钥脱敏、长字符串截断。

全部所选用例通过返回0；失败、执行异常或跳过返回1；命令行与提交前完整性预检错误返回2。

执行器的离线单元测试不会提交模型任务；其数量与上述7个接口场景用例分别统计：

```bash
test-cases/.venv/bin/python -m unittest discover -s test-cases/kling-v4 -v
```
