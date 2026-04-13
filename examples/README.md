# Examples

七牛 AIToken 平台 API 调用示例，按编程语言和 SDK 分类组织。

## 目录结构

```
examples/
├── python/
│   ├── anthropic-sdk/       # Anthropic Python SDK 示例（Messages API 直接调用）
│   ├── claude-agent-sdk/    # Claude Agent SDK 示例（Anthropic Messages API）
│   ├── fal-client-sdk/      # fal-client-sdk 示例（队列模式）
│   ├── genai-sdk/           # Google GenAI SDK 示例（Vertex AI 协议）
│   └── openai-sdk/          # openai-sdk 示例（Chat Completions 接口）
├── go/
│   └── openai-sdk/          # openai-go-sdk 示例（Chat Completions 接口）
├── typescript/
│   ├── ai-sdk-openai/             # Vercel AI SDK — OpenAI Provider
│   ├── ai-sdk-openai-compatible/  # Vercel AI SDK — OpenAI Compatible Provider
│   ├── ai-sdk-anthropic/          # Vercel AI SDK — Anthropic Provider
│   └── ai-sdk-google-vertex/      # Vercel AI SDK — Google Vertex Provider
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

## Python — genai-sdk

适用于 Gemini 系列模型，使用 [Google GenAI SDK](https://googleapis.github.io/python-genai/) 通过 Vertex AI 协议调用。

| 示例 | 模型 | 说明 |
|------|------|------|
| [gemini_basic.ipynb](python/genai-sdk/gemini_basic.ipynb) | Gemini 3.0 Pro | 基础用法：流式/非流式生成、系统指令、多轮对话 |
| [gemini_image_generation.ipynb](python/genai-sdk/gemini_image_generation.ipynb) | Gemini 3.1 Flash Image | 图片生成：文生图、图生图（单图/多图编辑） |

## Python — openai-sdk

适用于 Gemini、DeepSeek、Qwen 等兼容 OpenAI 接口的模型。

| 示例 | 模型 | 说明 |
|------|------|------|
| [gemini_3_pro_image_generation.ipynb](python/openai-sdk/gemini_3_pro_image_generation.ipynb) | Gemini 3.0 Pro Image | 文生图、图生图（单图/多图编辑） |
| [gemini_file_understanding.ipynb](python/openai-sdk/gemini_file_understanding.ipynb) | Gemini 3.0 Pro | 大文件上传 + 多模态理解（文件上传 → 轮询就绪 → Chat Completions） |

## Python — claude-agent-sdk

适用于通过 Anthropic Messages API 接入的 Claude 系列模型，使用 [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) 构建 AI Agent 应用。

| 示例 | 模型 | 说明 |
|------|------|------|
| [claude_agent_sdk_basic.ipynb](python/claude-agent-sdk/claude_agent_sdk_basic.ipynb) | Claude 4.6 Sonnet | Agent SDK 基础用法：一次性查询、自定义工具、多轮对话 |
| [claude_image_understanding.ipynb](python/claude-agent-sdk/claude_image_understanding.ipynb) | Claude 4.6 Sonnet | 图片理解：通过 Agent SDK 的 Read 工具读取本地图片并分析 |

## Python — anthropic-sdk

适用于通过 Anthropic Messages API 接入的 Claude 系列模型，使用 [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python) 直接调用。

| 示例 | 模型 | 说明 |
|------|------|------|
| [claude_basic.ipynb](python/anthropic-sdk/claude_basic.ipynb) | Claude 4.6 Sonnet | 基础用法：非流式/流式生成、系统提示词、多轮对话、参数调整 |
| [claude_image_understanding.ipynb](python/anthropic-sdk/claude_image_understanding.ipynb) | Claude 4.6 Sonnet | 图片理解：通过 Base64 编码传入本地图片，调用 Messages API 分析 |

## Go — openai-sdk

适用于 Gemini 等兼容 OpenAI 接口的模型，使用 [openai-go](https://github.com/openai/openai-go) SDK。

| 示例 | 模型 | 说明 |
|------|------|------|
| [gemini_file_understanding](go/openai-sdk/gemini_file_understanding/) | Gemini 3.0 Pro | 大文件上传 + 视频理解（文件上传 → 轮询就绪 → Chat Completions） |

## TypeScript — Vercel AI SDK

适用于通过 [Vercel AI SDK](https://ai-sdk.dev/) 调用各类 AI 模型的 TypeScript 示例。

| 示例 | SDK | 模型 | 说明 |
|------|-----|------|------|
| [openai_basic.ts](typescript/ai-sdk-openai/openai_basic.ts) | @ai-sdk/openai | OpenAI GPT-5.4 | 基础用法：非流式/流式生成、系统提示词、多轮对话、参数调优 |
| [openai_compatible_basic.ts](typescript/ai-sdk-openai-compatible/openai_compatible_basic.ts) | @ai-sdk/openai-compatible | OpenAI GPT-5.4 | 通用 OpenAI 兼容 Provider：无需 providerOptions 变通，更适合第三方 API |
| [anthropic_basic.ts](typescript/ai-sdk-anthropic/anthropic_basic.ts) | @ai-sdk/anthropic | Claude 4.6 Opus | 基础用法：非流式/流式生成、系统提示词、多轮对话、参数调优 |
| [gemini_basic.ts](typescript/ai-sdk-google-vertex/gemini_basic.ts) | @ai-sdk/google-vertex | Gemini 3.1 Pro | 基础用法：非流式/流式生成、系统提示词、多轮对话、参数调优 |

## JavaScript — fal-ai-sdk

> 即将推出

## JavaScript — openai-sdk

> 即将推出

## cURL

> 即将推出
