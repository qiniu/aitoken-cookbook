// file_upload.go — 文件上传 API 的原生 HTTP 调用逻辑（不依赖 OpenAI SDK）
//
// 七牛 AIToken 的 /files 接口用于将远程文件异步上传到 GCS，供 Gemini 等模型使用。
// 典型流程：创建上传任务 → 轮询等待状态变为 ready → 在 Chat Completions 中引用 file_id。

package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

const baseURL = "https://api.qnaigc.com/v1"

// ================== 文件上传相关结构体 ==================

// CreateFileRequest 创建文件上传任务的请求体
type CreateFileRequest struct {
	Model     string `json:"model"`
	SourceURL string `json:"source_url"`
	ExpiresIn int    `json:"expires_in,omitempty"` // 过期时间（秒），默认 172800（48小时）
}

// FileResponse 文件上传任务的响应体
type FileResponse struct {
	ID          string     `json:"id"`
	Object      string     `json:"object"`
	Status      string     `json:"status"` // pending, uploading, ready, failed, expired
	Model       string     `json:"model"`
	CreatedAt   int64      `json:"created_at"`
	SyncedAt    int64      `json:"synced_at,omitempty"`
	ExpiresAt   int64      `json:"expires_at"`
	FileName    string     `json:"file_name,omitempty"`
	FileSize    int64      `json:"file_size,omitempty"`
	ContentType string     `json:"content_type,omitempty"`
	Error       *FileError `json:"error,omitempty"`
}

// FileError 文件处理错误信息
type FileError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// ================== 文件上传与轮询 ==================

// createFile 创建文件上传任务，返回 file_id
// 该接口会将 source_url 指向的文件异步上传到 GCS，供 Gemini 模型使用
func createFile(apiKey, model, sourceURL string) (*FileResponse, error) {
	reqBody := CreateFileRequest{
		Model:     model,
		SourceURL: sourceURL,
	}
	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("序列化请求体失败: %w", err)
	}

	req, err := http.NewRequest("POST", baseURL+"/files", bytes.NewReader(bodyBytes))
	if err != nil {
		return nil, fmt.Errorf("创建请求失败: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+apiKey)

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("发送请求失败: %w", err)
	}
	defer resp.Body.Close()

	respBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("读取响应失败: %w", err)
	}

	if resp.StatusCode != http.StatusCreated && resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("创建文件失败, status=%d, body=%s", resp.StatusCode, string(respBytes))
	}

	var fileResp FileResponse
	if err := json.Unmarshal(respBytes, &fileResp); err != nil {
		return nil, fmt.Errorf("解析响应失败: %w", err)
	}
	return &fileResp, nil
}

// getFileStatus 查询文件处理状态
func getFileStatus(apiKey, fileID string) (*FileResponse, error) {
	req, err := http.NewRequest("GET", baseURL+"/files/"+fileID, nil)
	if err != nil {
		return nil, fmt.Errorf("创建请求失败: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+apiKey)

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("发送请求失败: %w", err)
	}
	defer resp.Body.Close()

	respBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("读取响应失败: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("查询文件状态失败, status=%d, body=%s", resp.StatusCode, string(respBytes))
	}

	var fileResp FileResponse
	if err := json.Unmarshal(respBytes, &fileResp); err != nil {
		return nil, fmt.Errorf("解析响应失败: %w", err)
	}
	return &fileResp, nil
}

// waitForFileReady 轮询等待文件处理完成
// 文件上传是异步的，需要轮询直到状态变为 ready
func waitForFileReady(apiKey, fileID string, timeout time.Duration) (*FileResponse, error) {
	deadline := time.Now().Add(timeout)
	interval := 3 * time.Second

	for time.Now().Before(deadline) {
		fileResp, err := getFileStatus(apiKey, fileID)
		if err != nil {
			return nil, err
		}

		fmt.Printf("  文件状态: %s\n", fileResp.Status)

		switch fileResp.Status {
		case "ready":
			return fileResp, nil
		case "failed":
			errMsg := "unknown error"
			if fileResp.Error != nil {
				errMsg = fileResp.Error.Message
			}
			return nil, fmt.Errorf("文件处理失败: %s", errMsg)
		case "expired":
			return nil, fmt.Errorf("文件已过期")
		}

		// pending 或 uploading 状态，继续等待
		time.Sleep(interval)
	}

	return nil, fmt.Errorf("等待文件就绪超时（%v）", timeout)
}

// uploadAndWait 创建文件上传任务并轮询等待就绪，打印完整的进度信息
// 这是 createFile + waitForFileReady 的便捷封装，适合在示例代码中直接调用
func uploadAndWait(apiKey, model, sourceURL string) (*FileResponse, error) {
	// 创建文件上传任务
	fmt.Println("\n--- 第一步：创建文件上传任务 ---")
	fileResp, err := createFile(apiKey, model, sourceURL)
	if err != nil {
		return nil, fmt.Errorf("创建文件失败: %w", err)
	}
	fmt.Printf("文件创建成功!\n")
	fmt.Printf("  file_id: %s\n", fileResp.ID)
	fmt.Printf("  status:  %s\n", fileResp.Status)

	// 轮询等待文件就绪
	fmt.Println("\n--- 第二步：等待文件处理完成 ---")
	fileResp, err = waitForFileReady(apiKey, fileResp.ID, 5*time.Minute)
	if err != nil {
		return nil, fmt.Errorf("等待文件就绪失败: %w", err)
	}
	fmt.Printf("文件已就绪!\n")
	fmt.Printf("  file_id:      %s\n", fileResp.ID)
	fmt.Printf("  file_name:    %s\n", fileResp.FileName)
	fmt.Printf("  file_size:    %d bytes\n", fileResp.FileSize)
	fmt.Printf("  content_type: %s\n", fileResp.ContentType)

	return fileResp, nil
}
