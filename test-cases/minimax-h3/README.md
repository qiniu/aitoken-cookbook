# MiniMax H3 视频生成兼容性测试

依据仓库内的 [`docs/minimax-h3.md`](../../docs/minimax-h3.md)，验证被测服务是否兼容 MiniMax 视频生成 V2 接口：

- 创建任务：`POST {API_BASE_URL}/v2/video_generation`
- 查询任务：`GET {API_BASE_URL}/v2/query/video_generation/{task_id}`
- 鉴权：`Authorization: Bearer {API_KEY}`

创建成功返回 `task_id`，脚本随后轮询查询接口，直到任务进入 `succeeded`、`failed` 或 `cancelled`。

## 覆盖范围

- `minimax-h3`：文生视频使用 `2K / 4 秒`，首帧图生视频使用 `768P / 15 秒`，合并覆盖两个分辨率档位和时长上下界。
- `minimax-h3-max`：文生视频使用 `480P / 5 秒`，首帧图生视频使用 `768P / 15 秒`，合并覆盖两个分辨率档位和时长上下界，并验证 `extra.prompt_expansion_mode`。
- 正向场景：文生视频、首帧图生视频、首尾帧视频、多模态参考视频。
- 负向场景：空 prompt、非法模型、文生视频使用 `adaptive`、H3-Max 请求 `2K`。

每个 profile 默认真正生成 4 段视频；负向用例应在创建阶段失败，不产生视频。回调、文件上传、`mm_file://`、Base64、H3-Context-IR 和视频再生成不在本套件范围内。

## 校验内容

响应结构通过 `schemas/` 下的 JSON Schema 校验；查询基础 Schema 仅限制文档声明的字段类型，不把文档未标注为必填的 `VideoTask` 字段设为必填。成功态必须满足后续业务断言，另外检查：

- 查询 `task.id` 与创建 `task_id` 一致。
- 查询结果的模型、分辨率和时长与请求一致。
- 查询结果的宽高比符合场景语义：文生视频回显指定比例，图生视频按 `adaptive` 处理。
- 成功任务为 `task_type=generation`，并返回 `content.url`；若响应包含 `modality`，则校验其为 `video`。
- 合并校验核心计费字段：`total_seconds = input_seconds + output_seconds`。
- `output_seconds` 等于生成时长，`input_image_count` 等于请求图片数。
- 无参考视频时 `input_seconds=0`；有参考视频时 `input_seconds>0`。参考音频不计入当前计费断言，`input_audio_seconds` 可省略。
- 错误响应至少包含 `error.message`；不强制要求顶层 `type`、`error.type`，`error.http_code` 若存在必须为三位 HTTP 状态码字符串，并与 HTTP 状态一致。

## 依赖

复用 `test-cases/requirements.txt` 中的 `pyyaml` 和 `jsonschema`：

```bash
bash test-cases/setup.sh
source test-cases/.venv/bin/activate
```

## 运行

```bash
cd test-cases/minimax-h3
export API_BASE_URL="https://api.minimax.cn"
export API_KEY="your-api-key"

python run_tests.py --profile minimax-h3
python run_tests.py --profile minimax-h3-max
```

`--profile` 描述能力，`--model` 决定实际写入请求体的模型名。测试兼容网关的自定义别名时：

```bash
python run_tests.py --profile minimax-h3 --model my-minimax-h3-alias
```

也可通过 `MINIMAX_H3_MODEL` 设置模型名，命令行 `--model` 优先。

仅检查请求构造和 Schema，不访问收费接口：

```bash
python run_tests.py --profile minimax-h3 --dry-run
python run_tests.py --profile minimax-h3-max --dry-run
```

创建后只查询一次、不等待视频完成：

```bash
python run_tests.py --profile minimax-h3 --no-poll
```

`--no-poll` 会自动跳过依赖成功终态的断言，只保留创建响应和首次查询的基础契约检查。

## 报告

默认在 `reports/` 下生成：

- `report.json`
- `report.md`
- `report.html`

报告包含脱敏环境变量、请求体、创建响应、轮询次数、最终查询响应、usage 和视频 URL。轮询过程响应只用于判断任务是否进入终态，不参与字段 Schema 或业务断言；仅最终查询响应参与校验。全部执行用例通过时退出码为 0；`skipped` 不影响最终结果。
