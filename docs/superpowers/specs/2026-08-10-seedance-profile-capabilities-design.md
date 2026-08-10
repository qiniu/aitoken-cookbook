# Seedance 多模型能力感知测试设计

## 背景

当前 `test-cases/seedance/` 通过 `--model` 指定被测模型，但所有用例共用同一组参数和执行集合。最新用例把 `t2v_basic` 固定为 `resolution: 4k`，因此切换到 Seedance 2.0 Fast、2.0 Mini 或 2.5 时，会把模型明确不支持的能力误报为兼容性失败。

同时，调用方可能向 `--model` 传 Endpoint ID 或自定义别名，不能依靠模型字符串可靠判断真实版本。测试套件需要将“请求中的模型标识”和“用于选择测试及合法参数的能力档案”分离。

权威能力来源：

- [创建视频生成任务 API](https://docs.volcengine.com/docs/82379/1520757?lang=zh)
- [Doubao Seedance 2.5 教程](https://docs.volcengine.com/docs/82379/2607688?lang=zh)
- [Doubao Seedance 2.0 系列教程](https://docs.volcengine.com/docs/82379/2291680?lang=zh)
- [模型列表中的视频生成能力](https://docs.volcengine.com/docs/82379/1330310?lang=zh#7571da3f)

## 目标

1. 使用必填 `--profile` 显式声明被测模型能力，不从 `--model` 推断。
2. 同一套 case 定义可安全测试 Seedance 2.5、2.0、2.0 Fast 和 2.0 Mini。
3. 支持的特性执行正向测试，不支持的特性显示为 `skipped`，而不是误报失败。
4. 将 2.5 的 30 秒、MOV、纯音频参考、更多参考素材及任务参数限制纳入默认测试。
5. 继续使用火山官网公开素材 URL，不提交 Base64 或本地媒体 fixture。
6. 报告明确记录实际 `model`、`profile`、跳过原因以及能力特有断言。

## 非目标

- 不根据 Endpoint ID 查询火山控制面以反推模型版本。
- 不测试价格、限流、生成质量或多语言语义质量；这些结果不适合作为稳定的协议兼容性断言。
- 不使用当前官方素材无法合法覆盖的“10 段视频”“10 段音频”和“50 个总素材”上限，避免用超时长输入制造必然失败的伪测试。
- 不改变 Seedance Assets 测试套件。

## 能力档案

新增独立的 `test-cases/seedance/profiles.yaml`，维护四个 profile：

| Profile | 分辨率 | 输出格式 | 最长时长 | 纯音频参考 | 图片上限 | 视频上限 | 音频上限 | 总素材上限 |
|---|---|---|---:|---:|---:|---:|---:|---:|
| `seedance-2.5` | 480p、720p | mp4、mov | 30 秒 | 是 | 30 | 10 | 10 | 50 |
| `seedance-2.0` | 480p、720p、1080p、4k | mp4 | 15 秒 | 否 | 9 | 3 | 3 | 15 |
| `seedance-2.0-fast` | 480p、720p | mp4 | 15 秒 | 否 | 9 | 3 | 3 | 15 |
| `seedance-2.0-mini` | 480p、720p | mp4 | 15 秒 | 否 | 9 | 3 | 3 | 15 |

Profile 同时声明 2.5 的任务参数限制：

- 首帧、首尾帧：`ratio: adaptive`。
- 视频编辑：`ratio: adaptive` 且 `duration: -1`。
- 视频延长：`ratio: adaptive`。

这些限制用于构造合法正向请求。测试不会通过发送已知非法参数来证明限制存在。

## 命令行契约

`--profile` 为必填参数，取值严格限制为上述四项；`--model` 保持现有含义，只负责写入请求体：

```bash
python run_tests.py \
  --model ep-custom-seedance-prod \
  --profile seedance-2.5
```

缺少或传入未知 profile 时，在发出任何网络请求前退出并显示可选值。报告运行变量新增 `SEEDANCE_PROFILE`，使 Endpoint ID 或别名的测试结果仍可复现。

## 用例选择与参数覆盖

Case 可声明结构化 `requires`，由 runner 对照 profile 判断是否执行。例如：

```yaml
requires:
  resolution: 4k
```

```yaml
requires:
  output_format: mov
```

```yaml
requires:
  min_max_duration: 30
  audio_only_reference: true
```

不满足 `requires` 时不构造请求，直接生成 `status: skipped` 的 `CaseResult`，并在 `details.skip_reason` 中记录具体能力差异。

Profile 特有的合法请求参数使用 case 内的 `profile_overrides` 表达，优先级为：

1. `profile_overrides[当前 profile]`
2. case 顶层显式参数
3. `cases.yaml` 全局默认值

全局 `ratio` 改为 `adaptive`，使现有首帧、首尾帧用例可直接兼容 2.5；需要固定比例的文生或参考生用例继续在 case 内显式写入。

## 默认测试集合

### 共有用例

现有文生、首帧、首尾帧、错误格式、真人拒绝和无效素材用例继续运行。文生能力参数按 profile 合并到单个 `t2v_full` 请求。

新增两个共有任务类型用例：

- 视频编辑：所有 profile 均执行；2.5 使用 `ratio: adaptive`、`duration: -1`，2.0 系列使用合法的普通时长。
- 视频延长：所有 profile 均执行；2.5 使用 `ratio: adaptive`。

### 按能力执行的用例

| 用例 | 执行 profile | 核心断言 |
|---|---|---|
| `t2v_full` | 全部 | 2.5 单次验证 30 秒 + MOV；2.0 标准版单次验证 4K；Fast / Mini 单次验证 720p、5 秒；均同时验证通用文生流程与尾帧 |
| `audio_only_reference` | `seedance-2.5` | 仅文本和参考音频即可成功 |
| `reference_images_profile_max` | 全部 | 单次合并图片上限与多模态：2.5 发送 30 图 + 1 视频 + 1 音频；2.0 系列发送 9 图 + 1 视频 + 1 音频 |
| `multimodal_reference_6_videos` | `seedance-2.5` | 1 图 + 6 视频、总视频时长约 28.14 秒并成功 |

Fast 和 Mini 不执行纯音频与 6 视频用例；文生请求自身使用合法的 720p、5 秒参数，不再为不支持的文生能力创建独立 skipped case。

默认执行预计真正出片次数：Seedance 2.5 为 9 次，Seedance 2.0 标准版、Fast、Mini 均为 7 次。另有 3 个创建阶段应失败的负向请求，不会出片。允许多个能力参数合并后在创建阶段直接失败，测试不要求把失败归因到某一个具体参数。

## 官方素材

所有新增素材都使用火山官网文档公开 URL：

### 图片上限

使用以下安全的官方产品参考图，按 profile 上限重复为 9 或 30 个独立 `content` 项：

- `https://arkdocs.tos-cn-beijing.volces.com/images/video-generation/seedance2.5_reference1.png`

重复 URL 仍形成独立请求项，用于验证接口接受对应数量；不声称验证了 9/30 个不同语义主体的生成质量。

### 六视频参考

使用官方 2.5 教程原有的 6 段参考视频：

- `https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference2.mp4`
- `https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference3.mp4`
- `https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference4.mp4`
- `https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference5.mp4`
- `https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference6.mp4`
- `https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference7.mp4`

本次设计阶段用 `ffprobe` 验证的时长依次约为 5.062、5.085、3.553、5.085、4.267、5.085 秒，总计约 28.14 秒，符合 2.5 的 30 秒总时长限制。

### 音频与编辑/延长

- 纯音频参考：`https://ark-project.tos-cn-beijing.volces.com/doc_audio/r2v_tea_audio1.mp3`
- 视频编辑：`https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_edit_input.mov`
- 视频延长：使用火山教程公开的 `r2v_extend_video*` URL。

当前官网可发现的适用 MP3/WAV 最短仍超过 6 秒，无法合法重复 10 次并保持总时长不超过 30 秒。因此本次不会添加 10 音频、10 视频或 50 总素材的误导性上限用例；profile 仍记录这些官方能力值。

## 请求构造

`build_create_body` 增加 `output_format` 透传。新增内容场景：

- `audio_only_reference`：`[text, reference_audio]`
- `video_edit`：`[text, reference_video]`，提示词明确触发编辑意图
- `video_extend`：`[text, reference_video]`，提示词明确触发延长意图
- `reference_images_profile_max`：根据 profile 生成 9 或 30 个 `reference_image`，并在同一请求加入 1 个 `reference_video` 与 1 个 `reference_audio`
- `multimodal_reference_6_videos`：使用官方 1 图 + 6 视频组合

请求构造阶段校验生成的素材数量没有超过 profile 声明值，配置错误应作为本地 `error` 返回，不发送请求。

## 新增断言

### 查询字段与请求一致

增加通用命名 check：

- `query_resolution_matches_request`
- `query_duration_matches_request`

它们从最终查询响应读取 `resolution` / `duration`，与实际创建请求比较。这样 4K 和 30 秒用例不再只以“任务成功”代替能力验证。

### MOV 产物验证

查询 API 不返回 `output_format`。MOV 用例取得 `content.video_url` 后，用标准库发起 Range 请求并只读取文件头少量字节：

- 必须存在 ISO Base Media File `ftyp` box；
- major brand 必须为 QuickTime `qt  `。

服务端忽略 Range 时客户端仍只读取固定长度后关闭连接，不下载完整视频。断言失败时报告 URL、响应 Content-Type 和已读取的文件头摘要。该检查只验证容器格式，不引入 `ffprobe` 运行依赖，也不额外断言编码器或色度采样。

## 报告

公共报告模型增加 `skipped` 状态并保持向后兼容：

- 汇总增加 `skipped` 数量；
- skipped 不影响最终 PASS/FAIL；
- Markdown/HTML 使用独立图标和样式；
- 其他套件没有 skipped case 时输出语义保持不变。

Seedance 报告环境区新增 profile。每个 case 的 details 继续保存最终创建请求，因此能复核 profile 覆盖后的真实参数和素材数量。

## 验证策略

实现时增加不访问真实 API 的自动化测试，覆盖：

1. 四个 profile 的加载、必填参数和未知值错误。
2. 4K、30 秒、MOV、纯音频及六视频用例的选择和跳过原因。
3. `profile_overrides` 优先级及 2.5 编辑/延长合法参数。
4. 9/30 图片项和六视频项的请求体结构、role、数量。
5. `output_format` 透传。
6. 查询 resolution/duration 匹配和不匹配。
7. MOV `ftyp/qt` 文件头识别及异常响应。
8. 公共报告 skipped 汇总、PASS 语义和三种输出格式。

本地验收至少运行：

```bash
python -m unittest discover
python test-cases/seedance/run_tests.py --dry-run --profile seedance-2.5
python test-cases/seedance/run_tests.py --dry-run --profile seedance-2.0
python test-cases/seedance/run_tests.py --dry-run --profile seedance-2.0-fast
python test-cases/seedance/run_tests.py --dry-run --profile seedance-2.0-mini
git diff --check
```

真实视频生成依赖用户提供的 `API_BASE_URL`、`API_KEY` 和可用模型/Endpoint，不作为无凭据本地验收的前置条件。若执行真实测试，应分别报告本地结构测试与远端生成结果，不能把未运行的远端测试描述为通过。
