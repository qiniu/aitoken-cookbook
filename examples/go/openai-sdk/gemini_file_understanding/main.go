// Gemini 文件理解示例 — 使用七牛 AIToken API 进行图片/视频多模态理解
//
// 运行方式：
//   export QINIU_API_KEY="your-api-key"
//   go run .
//
// 流程：通过 /files 接口上传远程文件 → 轮询等待就绪 → 使用 OpenAI Go SDK 调用 Chat Completions
//
// qfile ID 可通过以下两种方式传入：
//   - ImageContentPart：将 qfile ID 作为 URL 传入（适用于图片）
//   - FileContentPart：将 qfile ID 作为 FileID 传入（适用于所有文件类型）

package main

import (
	"context"
	"fmt"
	"os"

	"github.com/openai/openai-go/v3"
	"github.com/openai/openai-go/v3/option"
)

// chatWithImage 使用 OpenAI Go SDK 发送包含图片的对话请求
// 通过 image_url 类型传入 qfile ID，平台会自动将其替换为实际图片地址
// 注意：ImageContentPart 的 URL 字段不仅支持普通图片 URL，也支持 qfile ID
func chatWithImage(apiKey, model, fileID, prompt string) (string, error) {
	client := openai.NewClient(
		option.WithAPIKey(apiKey),
		option.WithBaseURL(baseURL),
	)

	completion, err := client.Chat.Completions.New(
		context.Background(),
		openai.ChatCompletionNewParams{
			Model: openai.ChatModel(model),
			Messages: []openai.ChatCompletionMessageParamUnion{
				openai.UserMessage([]openai.ChatCompletionContentPartUnionParam{
					openai.TextContentPart(prompt),
					openai.ImageContentPart(openai.ChatCompletionContentPartImageImageURLParam{
						URL: fileID, // 传入 qfile ID，如 "qfile-xxx-1770719212268100147-e0011b"
					}),
				}),
			},
		},
	)
	if err != nil {
		return "", fmt.Errorf("调用 Chat Completions 失败: %w", err)
	}

	if len(completion.Choices) == 0 {
		return "", fmt.Errorf("未返回任何结果")
	}
	return completion.Choices[0].Message.Content, nil
}

// chatWithFile 使用 OpenAI Go SDK 发送包含文件的对话请求
// 通过 file 类型传入 qfile ID，平台会自动将其替换为 GCS 地址
// 适用于所有文件类型（图片、视频、音频、文档等）
func chatWithFile(apiKey, model, fileID, prompt string) (string, error) {
	client := openai.NewClient(
		option.WithAPIKey(apiKey),
		option.WithBaseURL(baseURL),
	)

	completion, err := client.Chat.Completions.New(
		context.Background(),
		openai.ChatCompletionNewParams{
			Model: openai.ChatModel(model),
			Messages: []openai.ChatCompletionMessageParamUnion{
				openai.UserMessage([]openai.ChatCompletionContentPartUnionParam{
					openai.TextContentPart(prompt),
					openai.FileContentPart(openai.ChatCompletionContentPartFileFileParam{
						FileID: openai.String(fileID), // 传入 qfile ID，如 "qfile-xxx-1770719212268100147-e0011b"
					}),
				}),
			},
		},
	)
	if err != nil {
		return "", fmt.Errorf("调用 Chat Completions 失败: %w", err)
	}

	if len(completion.Choices) == 0 {
		return "", fmt.Errorf("未返回任何结果")
	}
	return completion.Choices[0].Message.Content, nil
}

func main() {
	// 从环境变量读取 API Key
	apiKey := os.Getenv("QINIU_API_KEY")
	if apiKey == "" {
		fmt.Println("请设置环境变量 QINIU_API_KEY")
		fmt.Println("  export QINIU_API_KEY=\"your-api-key\"")
		os.Exit(1)
	}

	model := "gemini-3.1-pro-preview"

	// ==================== 示例一：图片理解（通过 ImageContentPart 传入 qfile） ====================
	fmt.Println("========================================")
	fmt.Println("示例一：图片理解（ImageContentPart + qfile）")
	fmt.Println("========================================")

	imageURL := "https://aitoken-public.qnaigc.com/example/generate-video/running-man.jpg"
	fmt.Printf("图片 URL: %s\n", imageURL)

	imgFile, err := uploadAndWait(apiKey, model, imageURL)
	if err != nil {
		fmt.Printf("上传图片失败: %v\n", err)
		os.Exit(1)
	}

	// 使用 ImageContentPart + qfile 调用 Chat Completions
	fmt.Println("\n--- 第三步：调用 Gemini 进行图片理解（ImageContentPart） ---")
	imagePrompt := "这张图片里有什么？请详细描述。"
	fmt.Printf("提示词: %s\n", imagePrompt)
	fmt.Printf("file_id: %s\n\n", imgFile.ID)

	imageAnswer, err := chatWithImage(apiKey, model, imgFile.ID, imagePrompt)
	if err != nil {
		fmt.Printf("图片理解调用失败: %v\n", err)
		os.Exit(1)
	}

	fmt.Println("=== 模型回复 ===")
	fmt.Println(imageAnswer)

	// ==================== 示例二：视频理解（通过 FileContentPart 传入 qfile） ====================
	fmt.Println("\n========================================")
	fmt.Println("示例二：视频理解（FileContentPart + qfile）")
	fmt.Println("========================================")

	videoURL := "https://aitoken-public.qnaigc.com/example/generate-video/the-little-dog-is-running-on-the-lawn.mp4"

	videoFile, err := uploadAndWait(apiKey, model, videoURL)
	if err != nil {
		fmt.Printf("上传视频失败: %v\n", err)
		os.Exit(1)
	}

	// 使用 FileContentPart + qfile 调用 Chat Completions
	fmt.Println("\n--- 第三步：调用 Gemini 进行视频理解（FileContentPart） ---")
	videoPrompt := "这段视频里发生了什么？请详细描述。"
	fmt.Printf("提示词: %s\n", videoPrompt)
	fmt.Printf("file_id: %s\n\n", videoFile.ID)

	videoAnswer, err := chatWithFile(apiKey, model, videoFile.ID, videoPrompt)
	if err != nil {
		fmt.Printf("视频理解调用失败: %v\n", err)
		os.Exit(1)
	}

	fmt.Println("=== 模型回复 ===")
	fmt.Println(videoAnswer)
}
