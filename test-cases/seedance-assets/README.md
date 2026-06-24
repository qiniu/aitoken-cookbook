# Seedance 素材资产（Assets）API 兼容性测试

把接口地址指向被测的素材资产兼容服务，运行本脚本即可测试该接口，
并在 `reports/` 下生成报告。

校验目标是 **被测端点的 path、请求体、响应体、鉴权方式能完全兼容火山方舟
素材资产 API 格式**：

- 端点：`POST {API_BASE_URL}/?Action=<Action>&Version=2024-01-01`（所有操作同一 path，靠 `Action` 区分）
- 鉴权：**AK/SK 火山 Signature V4 签名**（HMAC-SHA256），而非 Bearer token
- 响应：火山信封 `{ResponseMetadata:{...,Error}, Result:{...}}`

格式权威来源（火山官方文档）：

- [私域虚拟人像素材资产库](https://www.volcengine.com/docs/82379/2333565?lang=zh)
- [私域真人人像素材资产](https://www.volcengine.com/docs/82379/2333589?lang=zh)
- [签名鉴权与调用示例](https://www.volcengine.com/docs/82379/1465834?lang=zh)

> 与视频生成套件（[../seedance/](../seedance/)）是两套不同范式：那套是
> `Bearer token` + 语义化 REST path + snake_case；本套是 `AK/SK 签名` +
> `Action` 风格 + PascalCase 字段 + 信封响应。

## 鉴权：火山 Signature V4

素材资产 API 用 AK/SK 签名鉴权。[volc_sign.py](volc_sign.py) 用 Python 标准库
（`hashlib`/`hmac`）实现火山 V4 签名，每次请求前注入 4 个头：
`Content-Type` / `X-Date` / `X-Content-Sha256` / `Authorization`。

固定 `region=cn-beijing`、`service=ark`，派生签名密钥的种子直接用 SK（BytePlus
风格）。签名所覆盖的头固定为 `content-type;host;x-content-sha256;x-date`。

## 校验方式

响应体结构用 **JSON Schema（draft 2020-12）** 校验，schema 文件作为「火山格式契约」，
放在 [schemas/](schemas/) 下：

| 文件 | 校验对象 |
|------|------|
| `envelope.schema.json` | 响应外层信封（`ResponseMetadata` 必存在） |
| `error.schema.json` | 错误响应（`ResponseMetadata.Error` 含 `Code`/`Message`） |
| `result_create_asset_group.schema.json` | CreateAssetGroup 的 `Result`（非空 `Id`） |
| `result_create_asset.schema.json` | CreateAsset 的 `Result`（非空 `Id`） |
| `result_get_asset.schema.json` | GetAsset 的 `Result`（`Status` enum 等） |
| `result_list_assets.schema.json` | ListAssets 的 `Result`（`Items`/分页） |
| `result_list_asset_groups.schema.json` | ListAssetGroups 的 `Result` |
| `result_create_visual_validate_session.schema.json` | CreateVisualValidateSession 的 `Result`（`BytedToken`/`H5Link`） |

可用的 check（含义见 [run_tests.py](run_tests.py)）：

| check | 含义 |
|------|------|
| `http_2xx` | HTTP 2xx |
| `envelope` | 响应通过信封 schema |
| `no_error` | `ResponseMetadata.Error` 为 null（成功响应） |
| `result_schema` | `Result` 通过该 step 声明的 result schema |
| `error_status_4xx` | HTTP 4xx（负向用例） |
| `error_schema` | 响应通过 `error.schema.json`（负向用例，校验错误格式） |

## 用例：有依赖的生命周期链

素材资产是「有依赖的生命周期链」，必须**串行执行**，后续 step 通过
`${group_id}` / `${asset_id}` 占位符引用前序 step 的输出。用例定义见
[cases.yaml](cases.yaml)：

1. `CreateAssetGroup` 建组 → 捕获 `group_id`
2. `CreateAsset`（传 `group_id` + 图片 URL + `AssetType=Image`）→ 捕获 `asset_id`
3. `GetAsset` 轮询素材状态至 `Status=Active`（异步预处理）
4. `ListAssets`（按 `group_id` 查）→ 校验 `Items`/分页
5. `ListAssetGroups` → 校验素材组列表
6. `CreateVisualValidateSession` 拉起真人认证 H5 会话 → 仅校验返回 `BytedToken`/`H5Link` 格式
7. 负向用例：用不存在的素材 Id 查 `GetAsset`，校验错误响应格式

前置生命周期 step 失败时，依赖它的后续 step 会被跳过（标记 error）。

### 真人认证的人工步骤（无法自动化）

真人人像的完整流程需终端客户用 `CreateVisualValidateSession` 返回的 `H5Link`
在手机上**完成刷脸认证**，认证通过后回调里带 `bytedToken`，再凭它调
`GetVisualValidateResult` 取 Asset Group ID。刷脸这步无法自动化，所以本套件
**只验证 `CreateVisualValidateSession` 拉起会话的响应格式**，不走完整认证，
也不自动断言 `GetVisualValidateResult`。

### 输入素材

上传素材（CreateAsset）需要一张可访问的图片 URL，配置在 `cases.yaml` 顶部的
`asset_image_url`，默认填火山官方文档示例素材。

> 被测服务需能访问该公网 URL。如环境访问不到，请替换为你自己的素材 URL。

## 依赖与环境

依赖 `pyyaml` + `jsonschema`（签名用标准库，无额外依赖）。复用 test-cases 共享
虚拟环境，在仓库根目录执行：

```bash
bash test-cases/setup.sh
source test-cases/.venv/bin/activate
```

## 运行

把地址指向被测服务，填入火山 AK/SK，然后运行：

```bash
export API_BASE_URL="https://your-domain.com/api/v3"   # 被测接口地址
export VOLC_ACCESS_KEY="your-access-key"               # 火山 AK
export VOLC_SECRET_KEY="your-secret-key"               # 火山 SK
python run_tests.py
```

环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `API_BASE_URL` | 无（必填） | 被测接口的基础地址 |
| `VOLC_ACCESS_KEY` | 无（必填） | 火山 Access Key |
| `VOLC_SECRET_KEY` | 无（必填） | 火山 Secret Key |

不打真实接口、仅自测「请求体构造、占位符替换与 schema 加载」（无需 AK/SK）：

```bash
python run_tests.py --dry-run
```

轮询间隔与超时在 `cases.yaml` 顶部配置（`poll_interval` / `poll_timeout`，单位秒）。

## 结果

运行后在 `reports/` 下生成 `report.json` / `report.md` / `report.html`
三份报告，格式见 [test-cases 总览](../README.md#结果格式)。
进程退出码：全部通过为 0，否则为 1。

每个 step 的 `details` 会完整记录请求与响应，便于失败定位：

- `action` / `request_body`：火山 Action 名与请求体
- `http_status` / `polls`：HTTP 状态、轮询次数
- `captured`：本 step 捕获并供后续引用的变量（如 `group_id` / `asset_id`）
- `response`：完整响应体（带签名的长 URL 等已截断，仅保留前 500 字符）
