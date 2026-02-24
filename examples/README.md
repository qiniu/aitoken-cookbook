# Examples

七牛 AIToken 平台 API 调用示例，按编程语言和 SDK 分类组织。

## 目录结构

```
examples/
├── python/
│   ├── fal-client-sdk/          # fal-client-sdk 示例（队列模式）
│   └── openai-sdk/          # openai-sdk 示例（Chat Completions 接口）
├── javascript/
│   ├── fal-ai-sdk/
│   └── openai-sdk/
└── curl/                    # cURL 示例（无 SDK 依赖）
```

## 前置条件

1. 注册 [七牛 AIToken](https://developer.qiniu.com/aitokenapi/12884/how-to-get-api-key) 并获取 API Key
2. 设置环境变量：
   ```bash
   export QINIU_API_KEY="your-api-key"
   ```

## Python — fal-ai-sdk

适用于 Kling、Flux、MiniMax 等通过 fal-ai 协议接入的模型。

| 示例 | 模型 | 说明 |
|------|------|------|
| [kling_o1_image.ipynb](python/fal-client-sdk/kling_o1_image.ipynb) | Kling O1 Image | 图生图：风格迁移、图像编辑、多图参考 |

## Python — openai-sdk

适用于 DeepSeek、Qwen 等兼容 OpenAI 接口的模型。

> 即将推出

## JavaScript — fal-ai-sdk

> 即将推出

## JavaScript — openai-sdk

> 即将推出

## cURL

> 即将推出
