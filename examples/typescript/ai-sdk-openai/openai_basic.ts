/**
 * OpenAI 基础用法 — Vercel AI SDK (@ai-sdk/openai)
 *
 * 本示例演示如何通过七牛 AIToken 平台，使用 Vercel AI SDK 的 OpenAI Provider
 * 调用 OpenAI 兼容模型进行文本生成。
 *
 * 功能覆盖：
 * - 非流式生成：一次性返回完整响应
 * - 流式生成：逐步返回响应内容，降低首字延迟
 * - 系统提示词：自定义模型角色和行为
 * - 多轮对话：使用 messages 数组传递历史消息
 * - 参数调优：temperature、maxTokens 等
 *
 * API 端点：https://api.qnaigc.com/v1
 * 适用模型：openai/gpt-5.4
 */

import { generateText, streamText, type ModelMessage } from "ai";
import { createOpenAI } from "@ai-sdk/openai";

// ============================================================
// 1. 环境配置
// ============================================================

// 七牛 AIToken 平台地址（OpenAI 兼容端点）
const BASE_URL = "https://api.qnaigc.com/v1";

// 从环境变量读取 API Key（或替换为你的 API Key）
const API_KEY = process.env.QINIU_API_KEY ?? "<your-api-key>";

// 使用的模型
const MODEL_ID = "openai/gpt-5.4";

// 创建 OpenAI Provider 实例，指向七牛 AIToken 平台
const openai = createOpenAI({
  baseURL: BASE_URL,
  apiKey: API_KEY,
});

// 获取模型实例（使用 .chat() 指定 Chat Completions API，
// 默认的 openai() 在 v3 中使用 Responses API，七牛平台不支持）
const model = openai.chat(MODEL_ID);

console.log("环境配置完成!");
console.log(`  API 端点: ${BASE_URL}`);
console.log(`  模型: ${MODEL_ID}`);

// ============================================================
// 2. 非流式生成
// ============================================================

console.log("\n========== 2. 非流式生成 ==========\n");

const { text, usage } = await generateText({
  model,
  prompt: "请用一句话介绍什么是大语言模型。",
});

console.log("=== 模型回复 ===");
console.log(text);
console.log("\n--- 用量信息 ---");
console.log(`输入 Tokens: ${usage.inputTokens}`);
console.log(`输出 Tokens: ${usage.outputTokens}`);

// ============================================================
// 3. 流式生成
// ============================================================

console.log("\n========== 3. 流式生成 ==========\n");

const streamResult = streamText({
  model,
  prompt: "请简要介绍 TypeScript 语言的三个核心优势。",
});

process.stdout.write("=== 流式输出 ===\n");
for await (const chunk of streamResult.textStream) {
  process.stdout.write(chunk);
}

// 获取最终用量信息
const streamUsage = await streamResult.usage;
console.log("\n\n--- 用量信息 ---");
console.log(`输入 Tokens: ${streamUsage.inputTokens}`);
console.log(`输出 Tokens: ${streamUsage.outputTokens}`);

// ============================================================
// 4. 系统提示词
// ============================================================

console.log("\n========== 4. 系统提示词 ==========\n");

// 注意：AI SDK 会将模型 ID 以 "o" 开头的模型（如 openai/gpt-5.4）识别为推理模型，
// 自动把 system 角色转为 developer 角色，但七牛平台不支持 developer 角色。
// 通过 providerOptions 设置 systemMessageMode 为 "system" 来解决此问题。
const systemResult = await generateText({
  model,
  system:
    "你是一位资深的 TypeScript 开发工程师，擅长用简洁清晰的方式解释技术概念。回答时请附上代码示例。",
  prompt: "什么是 TypeScript 的泛型（Generics）？",
  providerOptions: {
    openai: { systemMessageMode: "system" },
  },
});

console.log("=== 模型回复 ===");
console.log(systemResult.text);
console.log("\n--- 用量信息 ---");
console.log(`输入 Tokens: ${systemResult.usage.inputTokens}`);
console.log(`输出 Tokens: ${systemResult.usage.outputTokens}`);

// ============================================================
// 5. 多轮对话
// ============================================================

console.log("\n========== 5. 多轮对话 ==========\n");

// 维护对话历史
const conversation: ModelMessage[] = [];

async function chat(userMessage: string): Promise<string> {
  // 将用户消息加入历史
  conversation.push({ role: "user", content: userMessage });

  const result = await generateText({
    model,
    system: "你是一位旅行顾问，擅长推荐旅行目的地和规划行程。",
    messages: conversation,
    providerOptions: {
      openai: { systemMessageMode: "system" },
    },
  });

  // 将模型回复加入历史，保持上下文
  conversation.push({ role: "assistant", content: result.text });

  return result.text;
}

// 第一轮对话
console.log("=== 第一轮 ===");
const question1 = "我想在五月份去一个适合拍照的地方旅行，有什么推荐？";
console.log(`用户: ${question1}`);
const reply1 = await chat(question1);
console.log(`助手: ${reply1}`);

// 第二轮对话（模型会记住上一轮的推荐内容）
console.log("\n=== 第二轮 ===");
const question2 = "你推荐的第一个地方，帮我规划一个 3 天的行程吧。";
console.log(`用户: ${question2}`);
const reply2 = await chat(question2);
console.log(`助手: ${reply2}`);

console.log(`\n--- 对话历史长度: ${conversation.length} 条消息 ---`);

// ============================================================
// 6. 参数调优
// ============================================================

console.log("\n========== 6. 参数调优 ==========\n");

// 低温度：更确定性的输出，适合事实性问答
const lowTempResult = await generateText({
  model,
  maxOutputTokens: 256,
  temperature: 0,
  prompt: "TypeScript 中 interface 和 type 的区别是什么？请用一句话总结。",
});

console.log("=== 低温度 (temperature=0) ===");
console.log(lowTempResult.text);

// 高温度：更多样化的输出，适合创意写作
const highTempResult = await generateText({
  model,
  maxOutputTokens: 256,
  temperature: 1,
  prompt: "请用一个比喻来描述编程。",
});

console.log("\n=== 高温度 (temperature=1) ===");
console.log(highTempResult.text);
