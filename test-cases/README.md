# Test Cases

模型接口兼容性测试用例集，按模型分目录组织。

这些用例是对接口行为的契约定义（请求 + 断言 + 预期值）。把接口地址指向被测服务，
运行用例即可测试该接口，并生成结构化报告。

## 目录结构

```
test-cases/
├── README.md
├── setup.sh                 # 一键创建共享虚拟环境（.venv）并安装依赖
├── requirements.txt         # 各模型脚本共用的第三方依赖
├── _shared/                 # 公共工具
│   ├── report.py            # 报告模块：统一结果格式，一次产出 json/md/html
│   └── gpt_image_2_token_calculator.py  # gpt-image-2 输出 token 计算工具
├── gpt-image-2/             # gpt-image-2 输出 token 校验测试（generations + edits）
│   ├── README.md            # 该模型的测试说明
│   ├── cases.yaml           # 用例定义
│   ├── run_tests.py         # 执行入口
│   ├── fixtures/            # 测试素材（edits 端点的输入图片）
│   └── reports/             # 运行结果输出目录（git 忽略）
├── seedance/                # Seedance 视频生成火山兼容格式测试（创建→轮询→查询）
│   ├── README.md            # 该模型的测试说明
│   ├── cases.yaml           # 用例定义（4 场景 + 负向用例）
│   ├── run_tests.py         # 执行入口（异步：创建任务 + 轮询查询）
│   ├── schemas/             # 火山格式响应体 JSON Schema（结构契约）
│   └── reports/             # 运行结果输出目录（git 忽略）
└── <model-name>/            # 其余模型按相同结构组织
    ├── README.md            # 该模型的测试说明
    ├── cases.yaml           # 用例定义
    ├── run_tests.py         # 执行入口
    ├── fixtures/            # 测试素材（图片、视频等）
    └── reports/             # 运行结果输出目录（git 忽略）
```

## 与 examples 的区别

| 目录 | 用途 |
|------|------|
| [examples/](../examples/) | 面向开发者的 API 调用示例，帮助快速上手 |
| `test-cases/` | 接口兼容性测试用例，批量执行并输出结构化结果 |

可参考 `examples/` 中的实现，但此处强调**可批量运行、有明确通过标准、可导出报告**。

## 前置条件

1. 准备好被测接口的地址与鉴权密钥
2. 设置环境变量（指向被测服务）：
   ```bash
   export API_BASE_URL="https://your-domain.com/v1"
   export API_KEY="your-api-key"
   ```
3. 创建虚拟环境并安装依赖（各模型脚本共用一个 `.venv`）：
   ```bash
   bash test-cases/setup.sh        # 创建 .venv（已存在则复用）并安装依赖
   bash test-cases/setup.sh -f     # 强制删除并重建 .venv
   source test-cases/.venv/bin/activate
   ```
   依赖在 [requirements.txt](requirements.txt) 中定义（`pyyaml`、`jsonschema`）；
   `.venv/` 已被 git 忽略，不纳入版本控制。

> 具体环境变量以各模型目录的 README 为准。

## 如何运行

先按前置条件创建并激活虚拟环境，再进入对应模型目录执行测试脚本：

```bash
source test-cases/.venv/bin/activate   # 若尚未激活
cd test-cases/<model-name>
python run_tests.py
```

运行完成后，结果会输出到 `reports/` 目录，每次运行同时产出三种格式（见下文）。

## 结果格式

所有模型统一复用 [_shared/report.py](_shared/report.py)，遵循 **「固定骨架 + 自由 details」** 的设计：公共逻辑只依赖每个 case 的固定元字段，模型特有数据放进自由的 `details`，互不干扰。

每次运行在 `reports/` 下生成三份报告：

| 文件 | 面向 | 说明 |
|------|------|------|
| `report.json` | 代码 / 机器 | 唯一事实源，便于程序解析与 diff |
| `report.md`   | 人类速览 | GitHub / 编辑器里直接看的表格 |
| `report.html` | 人类富展示 | 自包含页面；`details` 中的图片 / 视频 URL 自动内嵌预览 |

JSON 顶层固定为 `model` / `summary` / `cases`；每个 case 的固定字段：

| 字段 | 含义 |
|------|------|
| `id` | 用例唯一标识 |
| `name` | 可读名称 |
| `status` | `pass` / `fail` / `error` |
| `expected` | 期望值 |
| `actual` | 实际值 |
| `error` | 执行报错信息（仅 error 状态） |
| `duration_ms` | 耗时（毫秒） |
| `details` | 模型自定义数据（请求参数、媒体 URL、原始响应片段等） |

模型的 `run_tests.py` 只需收集 `CaseResult` 列表，构造 `Report` 后调用 `report.write(out_dir)` 即可：

```python
import sys
sys.path.insert(0, "../_shared")
from report import CaseResult, Report

cases = [CaseResult(id="low_1024", name="low 1024x1024",
                    status="pass", expected=255, actual=255)]
Report(model="gpt-image-2", cases=cases).write("reports")
```

## 结果提交

1. 运行全部 case
2. 将 `reports/` 下的结果文件（`report.json` / `report.md` / `report.html`，及必要的截图、日志）打包提交
3. 如有 case 失败，附上失败 case 的 ID 和 `error` 信息

## 公共工具（_shared/）

### gpt_image_2_token_calculator.py

复刻 OpenAI 官方文档算法，根据画质（quality）与图片宽高计算 gpt-image-2 的图像**输出 token** 数，用于校验计费侧的 output_token 是否正确。

```bash
# 用法：quality(low|medium|high) width height，均可省略（默认 low 1024 1024）
python _shared/gpt_image_2_token_calculator.py high 1024 1024

# 输出 JSON
python _shared/gpt_image_2_token_calculator.py --json medium 1536 1024
```

尺寸约束（不满足时报错并退出码 2）：宽高均需为 16 的倍数；像素总数在 655,360 ~ 8,294,400 之间；最长边 ≤ 3840px；长宽比 ≤ 3:1。

## 添加新模型测试

1. 在 `test-cases/` 下新建 `<model-name>/` 目录
2. 编写 `cases.yaml` 定义用例
3. 编写 `run_tests.py` 作为执行入口
4. 补充该目录的 `README.md`
5. 若引入了新的第三方依赖，追加到根目录 [requirements.txt](requirements.txt)，并重跑 `setup.sh` 安装
