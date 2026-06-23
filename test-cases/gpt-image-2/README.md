# gpt-image-2 兼容性测试

把接口地址指向被测的 gpt-image-2 兼容服务，运行本脚本即可测试该接口，
并在 `reports/` 下生成报告。

校验目标是 gpt-image-2 的两个图片接口，重点是
**计费侧 `usage.output_tokens` 是否符合 gpt-image-2 官方算法**：

- 文生图 `POST /v1/images/generations`（JSON 请求）
- 图生图 `POST /v1/images/edits`（`multipart/form-data`，需上传输入图片）

预期 token 值不写死：由 [_shared/gpt_image_2_token_calculator.py](../_shared/gpt_image_2_token_calculator.py)
按 `quality + size` 依官方算法动态算出，再与接口返回的 `usage.output_tokens`
比对。这是被测接口需对齐的计费契约。

## 用例

用例定义见 [cases.yaml](cases.yaml)，对 `generations` 与 `edits` 两个端点
各覆盖一组：

- 基础连通性
- `size` / `quality` 参数回显
- `output_tokens` 精确校验（low=196 / medium=1756 / high=7024，均 1024x1024）

每个 case 通过 `endpoint` 字段（`generations` / `edits`）选择端点。`edits`
端点需要一张输入图片，默认用 [fixtures/input.png](fixtures/input.png)（一张
1024x1024 的纯色合成 PNG，可在 cases.yaml 顶部的 `edit_image` 或单个 case 的
`image` 字段中替换为真实图片）。

## 依赖

```bash
pip install pyyaml
```

HTTP 请求使用标准库 `urllib`，无需安装 requests。

## 运行

把地址指向被测服务，填入密钥，然后运行：

```bash
export API_BASE_URL="https://your-domain.com/v1"   # 被测接口地址
export API_KEY="your-api-key"                      # 被测接口密钥
python run_tests.py
```

所有 case 默认**并发**请求（生图较慢，串行会很耗时），报告顺序仍与
`cases.yaml` 定义一致。

环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `API_BASE_URL` | 无（必填） | 被测接口的基础地址 |
| `API_KEY` | 无（必填） | 被测接口的鉴权密钥 |
| `GPT_IMAGE_MODEL` | `gpt-image-2` | 被测模型 id（也可用 `--model` 覆盖） |

被测模型 id 可自定义，命令行参数优先于环境变量：

```bash
python run_tests.py --model your-model-name
```

不打真实接口、仅自测「预期值计算链路」（无需配置地址和密钥）：

```bash
python run_tests.py --dry-run
```

## 结果

运行后在 `reports/` 下生成 `report.json` / `report.md` / `report.html`
三份报告，格式见 [test-cases 总览](../README.md#结果格式)。
进程退出码：全部通过为 0，否则为 1。

每个 case 的 `details` 会完整记录本次请求与响应，便于失败定位：

- `request`：请求 URL 与请求体
- `response`：完整响应体（base64 图片等超长字符串会截断，仅保留前 500 字符）
- `usage` / `expected_output_tokens`：实际计费返回与预期值

提交结果时打包 `reports/` 下的三份报告；如有 case 失败，
请一并附上失败 case 的 ID 与 `error` 信息。
