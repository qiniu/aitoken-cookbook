# Test Cases

模型接入 API 测试用例集，按模型分目录组织。每个目录包含一组可执行的测试 case，用于验证接口行为是否符合预期。

## 目录结构

```
test-cases/
├── README.md
├── _shared/                 # 公共工具（runner、结果格式等，按需添加）
│   └── gpt_image_2_token_calculator.py  # gpt-image-2 输出 token 计算工具
└── <model-name>/            # 按模型分目录，例如 kling、vidu
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
| `test-cases/` | 面向接入方的测试用例，批量执行并输出结构化结果 |

可参考 `examples/` 中的实现编写测试脚本，但此处强调**可批量运行、有明确通过标准、可导出报告**。

## 前置条件

1. 获取测试环境的 API Key
2. 设置环境变量：
   ```bash
   export QINIU_API_KEY="your-api-key"
   ```
3. 安装依赖（各模型目录的 README 中会说明具体依赖）

## 如何运行

进入对应模型目录，执行测试脚本：

```bash
cd test-cases/<model-name>
python run_tests.py
```

运行完成后，结果会输出到 `reports/` 目录（如 `report.json`）。

## 如何提交结果

1. 运行全部 case
2. 将 `reports/` 下的结果文件（及必要的截图、日志）打包发送
3. 如有 case 失败，附上失败 case 的 ID 和错误信息

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
