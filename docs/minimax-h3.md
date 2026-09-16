> ## Documentation Index
> Fetch the complete documentation index at: https://platform.minimaxi.com/docs/llms.txt
> Use this file to discover all available pages before exploring further.

# 创建视频生成任务

> 视频生成 V2 接口，通过多模态 content 数组输入（文本 / 图片 / 视频 / 音频），可通过 `model` 字段切换 MiniMax H3 与 MiniMax H3 Max，覆盖文生视频、图生视频（首尾帧）、多模态参考生视频，最高 2K 输出。<br /><br />提示：若需要使用 MiniMax H3 或 MiniMax H3 Max，请点击 [按量购买 API](/docs/guides/pricing-paygo#%E8%A7%86%E9%A2%91)。



## OpenAPI

````yaml api-reference/video/generation/api/v2-video-generation.json POST /v2/video_generation
openapi: 3.1.0
info:
  title: MiniMax API
  description: MiniMax video generation V2 (Hailuo-03) API
  license:
    name: MIT
  version: 2.0.0
servers:
  - url: https://api.minimax.cn
security:
  - bearerAuth: []
paths:
  /v2/video_generation:
    post:
      tags:
        - Video V2
      summary: 创建视频生成任务
      description: >-
        创建视频生成任务。模型依据传入的文本、图片、视频、音频等多模态信息生成视频。本接口为异步接口，创建成功后返回
        `task_id`，需通过[查询任务](/api-reference/video-generation-v2-query)接口轮询任务状态，任务成功后获取生成视频。


        当前支持模型：`MiniMax-H3`、`MiniMax-H3-Max`。
      operationId: videoGenerationV2Create
      parameters:
        - name: Content-Type
          in: header
          required: true
          description: 请求体的媒介类型，请设置为 `application/json`。
          schema:
            type: string
            enum:
              - application/json
            default: application/json
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/VideoGenerationV2Req'
            examples:
              文生视频 (t2va):
                value:
                  model: MiniMax-H3
                  content:
                    - type: text
                      text: >-
                        史诗级太空歌剧院线预告：女舰长独自站在巨大观景窗前，最后一支舰队正在集结并跃迁离去，强光爆闪、舰桥震动，她被留在原地。
                  resolution: 2K
                  duration: 5
                  ratio: '16:9'
              图生视频 (i2va):
                value:
                  model: MiniMax-H3
                  content:
                    - type: text
                      text: >-
                        Pull focus to the people in the background and add more
                        steam to the ramen bowl.
                    - type: image_url
                      image_url:
                        url: >-
                          https://cdn.hailuoai.com/prod/hailuo_demo/testsets/H3_AA_I2VA/gallery/sr_v17_variants_seed42_43_20260724/inputs/4a3a90bf9100_KDmcbkhzYo5sjjxr9FqcVmWVnzb.png
                      role: first_frame
                  resolution: 2K
                  duration: 5
                  ratio: adaptive
              多模态参考生视频 (r2va):
                value:
                  model: MiniMax-H3
                  content:
                    - type: text
                      text: >-
                        角色说话：Follow the wind, live free.Leave worries behind,
                        enjoy the moment，音色参考音频1
                    - type: video_url
                      video_url:
                        url: >-
                          https://cdn.hailuoai.com/prod/hailuo_demo/testsets/h3_promo_eval_ref2va/gallery/sr_v2p26_trio_seed42_20260724/inputs/297573323635_00_%E8%A7%86%E9%A2%911_YnyRbxEwio_video_20260525_163755_1927e9d3.mp4
                      role: reference_video
                    - type: audio_url
                      audio_url:
                        url: >-
                          https://cdn.hailuoai.com/prod/hailuo_demo/testsets/h3_promo_eval_ref2va/gallery/sr_v2p26_trio_seed42_20260724/inputs/f463d523c5ce_01_%E9%9F%B3%E9%A2%911_RSLcbpzJPo_6%E6%9C%885%E6%97%A5(1).mp3
                      role: reference_audio
                  resolution: 2K
                  duration: 5
                  ratio: adaptive
        required: true
      responses:
        '200':
          description: >-
            创建成功后返回 `task_id`。使用该 `task_id`
            调用[查询任务](/api-reference/video-generation-v2-query)接口获取任务状态与结果。


            **查询任务成功响应示例**


            ```json

            {
              "task": {
                "id": "424010985738629",
                "model": "MiniMax-H3",
                "status": "succeeded",
                "created_at": 1785125529,
                "updated_at": 1785125946,
                "content": {
                  "url": "https://your-cdn.example.com/h3-generated-2k-output.mp4"
                },
                "resolution": "2K",
                "duration": 5,
                "usage": {
                  "total_seconds": 5,
                  "input_seconds": 0,
                  "output_seconds": 5,
                  "input_image_count": 0
                },
                "ratio": "16:9",
                "task_type": "generation",
                "modality": "video"
              }
            }

            ```
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/VideoGenerationV2Resp'
        '400':
          $ref: '#/components/responses/Err400'
        '401':
          $ref: '#/components/responses/Err401'
        '402':
          $ref: '#/components/responses/Err402'
        '422':
          $ref: '#/components/responses/Err422'
        '429':
          $ref: '#/components/responses/Err429'
        '500':
          $ref: '#/components/responses/Err500'
components:
  schemas:
    VideoGenerationV2Req:
      type: object
      required:
        - model
        - content
        - resolution
        - duration
      properties:
        model:
          type: string
          description: >-
            模型名称。当前可用值：`MiniMax-H3`、`MiniMax-H3-Max`。


            - **`MiniMax-H3`**：支持文生视频、图生视频（首帧 / 尾帧）与多模态参考生视频；分辨率 `768P` /
            `2K`，时长 4~15 秒。

            - **`MiniMax-H3-Max`**：**极速生成**版本。支持**文生视频**、**图生视频（首帧 /
            尾帧）**与**多模态参考生视频**（参考图 / 参考视频 / 参考音频）；分辨率 `480P` / `768P`，**不支持
            `2K`**；时长 5~15 秒。
          enum:
            - MiniMax-H3
            - MiniMax-H3-Max
        content:
          type: array
          description: >-
            多模态输入内容数组，描述用于生成视频的信息。每个元素通过 `type` 区分类型（`text` / `image_url` /
            `video_url` / `audio_url`），并可通过 `role` 标注用途。


            **每次请求必须包含一个非空 `text` 项（prompt 必填）**；缺失会返回参数错误。


            支持的输入组合（对应不同生成场景）：

            - **文生视频**：仅一个 `text` 元素。

            - **图生视频-首帧**：`text` + 1 张 `image_url`（`role=first_frame` 或不填）。

            - **图生视频-尾帧**：`text` + 1 张 `image_url`（`role=last_frame`）。

            - **图生视频-首尾帧**：`text` + 2 张 `image_url`（`role` 分别为
            `first_frame`、`last_frame`）。

            - **多模态参考生视频**：`text` + 参考图片（`role=reference_image`）+
            参考视频（`role=reference_video`）+ 参考音频（`role=reference_audio`）的组合。


            > **图生视频与多模态参考生视频互斥**：content 中出现 `reference_image` /
            `reference_video` / `reference_audio` 任一 role，就不能再出现 `first_frame` /
            `last_frame`（反之亦然），二者不可混用。


            ---


            **输入媒体限制**（请求体总大小 ≤ 64 MB，大文件请用公网 URL，勿用 Base64）


            图片 `image_url`：


            | 项 | 限制 |

            | :--- | :--- |

            | 格式 | JPG、JPEG、PNG、WEBP、HEIC、HEIF |

            | 单文件大小 | ≤ 30 MB |

            | 宽高范围 | [256, 5760] px |

            | 长宽比（宽/高） | [0.4, 2.5] |

            | 数量 | 首帧 ≤ 1、尾帧 ≤ 1、参考图 ≤ 9 |


            视频 `video_url`（仅多模态参考场景）：


            | 项 | 限制 |

            | :--- | :--- |

            | 容器 / 格式 | MP4（`.mp4`）、MOV（`.mov`） |

            | 编码 | 视频 H.264/AVC、H.265/HEVC；音频 AAC、MP3 |

            | 单文件大小 | ≤ 50 MB |

            | 个数 | ≤ 3 |

            | 单段时长 | [2, 15] s；总时长 ≤ 15 s |

            | 宽高范围 | [256, 5760] px |

            | 长宽比（宽/高） | [0.4, 2.5] |

            | 帧率 | [23.976, 60] |


            音频 `audio_url`（仅多模态参考场景）：


            | 项 | 限制 |

            | :--- | :--- |

            | 格式 | WAV、MP3 |

            | 单文件大小 | ≤ 15 MB |

            | 个数 | ≤ 3 |

            | 单段时长 | [2, 15] s；总时长 ≤ 15 s |
          items:
            $ref: '#/components/schemas/ContentItem'
        resolution:
          type: string
          description: |-
            视频分辨率。不同模型支持的档位不同：

            - **`MiniMax-H3`**：`768P`、`2K`
            - **`MiniMax-H3-Max`**：`480P`、`768P`（默认 `768P`，不支持 `2K`）
          enum:
            - 480P
            - 768P
            - 2K
        duration:
          type: integer
          description: |-
            生成视频时长（秒），必选，整数。不同模型可用值不同：

            - **`MiniMax-H3`**：`4`~`15`
            - **`MiniMax-H3-Max`**：`5`~`15`（不支持 4 秒）
          enum:
            - 4
            - 5
            - 6
            - 7
            - 8
            - 9
            - 10
            - 11
            - 12
            - 13
            - 14
            - 15
        ratio:
          type: string
          description: >-
            生成视频的宽高比，默认 `adaptive`（自动，由输入自适应选择最合适的宽高比，实际比例可在查询接口的 `ratio`
            字段获取）。可用值：`adaptive`、`21:9`、`16:9`、`4:3`、`1:1`、`3:4`、`9:16`。


            **文生视频（t2va，content 仅含 `text`）**：`ratio` 必填，且不能为 `adaptive`；可用值
            `21:9`、`16:9`、`4:3`、`1:1`、`3:4`、`9:16`。


            **图生视频（i2va，content 含 `first_frame` / `last_frame`
            图片）**：宽高比由输入图片决定，`ratio` 恒为 `adaptive`；传入其他合理值不会报错，但会被忽略并按
            `adaptive` 处理。


            **多模态参考生视频（r2va，content 含 `reference_image` / `reference_video` /
            `reference_audio`）**：`ratio` 可选，默认 `adaptive`；也可显式指定上述任一具体比例。
          enum:
            - adaptive
            - '21:9'
            - '16:9'
            - '4:3'
            - '1:1'
            - '3:4'
            - '9:16'
        extra:
          type: object
          description: >-
            `MiniMax-H3-Max` 的额外生成选项。当前仅支持 `prompt_expansion_mode`。不传 `extra`
            或不传此属性时，使用 `balanced`。不要传 `balance`、空字符串、布尔值或其他未声明字段。
          properties:
            prompt_expansion_mode:
              type: string
              description: >-
                提示词扩写模式。`disabled` 表示关闭提示词扩写；`balanced` 表示均衡扩写且为默认值；`quality`
                表示质量优先扩写。
              enum:
                - disabled
                - balanced
                - quality
              default: balanced
          additionalProperties: false
        callback_url:
          type: string
          description: >-
            任务状态变更的回调通知地址。配置后 MiniMax 服务器会先发送含 `challenge` 字段的验证请求（需 3 秒内原样返回
            `challenge` 完成验证），验证成功后每当任务状态变更即向该地址 POST
            推送，推送体结构与[查询任务](/api-reference/video-generation-v2-query)接口的响应一致。


            回调 `status`
            取值：`queued`（排队中）、`running`（运行中）、`succeeded`（成功）、`failed`（失败）、`cancelled`（已取消）。
        aigc_watermark:
          type: boolean
          description: 是否在生成视频中添加 AIGC 标识水印，默认 `false`。
      example:
        model: MiniMax-H3
        content:
          - type: text
            text: 一个男孩在海边打篮球
        resolution: 2K
        duration: 5
        ratio: '16:9'
    VideoGenerationV2Resp:
      type: object
      properties:
        task_id:
          type: string
          description: 任务 ID，用于后续查询任务状态与结果。
      example:
        task_id: '424010985738629'
    ContentItem:
      type: object
      required:
        - type
      properties:
        type:
          type: string
          description: 输入内容的类型。
          enum:
            - text
            - image_url
            - video_url
            - audio_url
        text:
          type: string
          description: >-
            文本提示词（prompt），**必填**：所有场景都需包含一个非空 `text`，描述期望生成的视频。按字符数计算长度，单个
            `text` 最多 7000 个字符。
        image_url:
          type: object
          description: 当 `type=image_url` 时的图片对象（格式 / 大小 / 尺寸 / 数量限制见上方 content 说明）。
          required:
            - url
          properties:
            url:
              type: string
              description: >-
                图片地址,支持:公网 URL;`mm_file://{file_id}`(引用平台已有文件,如上传或历史产物的
                file_id);`data:image/<格式>;base64,<Base64>` data URI(`<格式>` 小写)。
        video_url:
          type: object
          description: >-
            当 `type=video_url` 时的视频对象（参考视频，仅多模态参考场景；格式 / 大小 / 时长限制见上方 content
            说明）。
          required:
            - url
          properties:
            url:
              type: string
              description: >-
                视频地址,支持:公网 URL;`mm_file://{file_id}`(引用平台已有文件的
                file_id);`data:video/mp4;base64,<Base64>` data URI。注意请求体总大小 ≤ 64
                MB、Base64 会放大约 33%,大视频请用公网 URL 或 mm_file://。
        audio_url:
          type: object
          description: >-
            当 `type=audio_url` 时的音频对象（参考音频，仅多模态参考场景；格式 / 大小 / 时长限制见上方 content
            说明）。
          required:
            - url
          properties:
            url:
              type: string
              description: >-
                音频地址,支持:公网 URL;`mm_file://{file_id}`(引用平台已有文件的
                file_id);`data:audio/<格式>;base64,<Base64>` data URI(`<格式>` 小写)。
        role:
          type: string
          description: |-
            内容的位置或用途，条件必填：
            - `first_frame`：首帧图片（图生视频；仅一张图且不填 role 时默认按 first_frame 处理）。
            - `last_frame`：尾帧图片（图生视频-首尾帧，需与 first_frame 成对）。
            - `reference_image`：参考图片（多模态参考生视频）。
            - `reference_video`：参考视频（多模态参考生视频）。
            - `reference_audio`：参考音频（多模态参考生视频）。
          enum:
            - first_frame
            - last_frame
            - reference_image
            - reference_video
            - reference_audio
    OaiError:
      type: object
      description: OpenAI 风格错误响应。出错时 HTTP 状态码为真实错误码(401/400/429/402/422/500…),响应体为该结构。
      properties:
        type:
          type: string
          description: 固定为 `error`。
          example: error
        error:
          $ref: '#/components/schemas/OaiErrorDetail'
        request_id:
          type: string
          description: 请求追踪 ID(便于排查)。
    OaiErrorDetail:
      type: object
      properties:
        type:
          type: string
          description: >-
            错误类型:`authorized_error`(401)/`bad_request_error`(400)/`rate_limit_error`(429)/`insufficient_balance_error`(402)/`unprocessable_entity_error`(422)/`overloaded_error`(529)/`server_error`(500)
            等。
        message:
          type: string
          description: 错误详情,结尾括号内为内部错误码(如 `... (1004)`)。
        http_code:
          type: string
          description: HTTP 状态码字符串,如 `401`。
  responses:
    Err400:
      description: 参数错误
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/OaiError'
          example:
            type: error
            error:
              type: bad_request_error
              message: >-
                invalid params, content must include a non-empty text item
                (prompt is required) (2013)
              http_code: '400'
            request_id: 021785229015510a2c883cf675b9804d
    Err401:
      description: 鉴权失败
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/OaiError'
          example:
            type: error
            error:
              type: authorized_error
              message: >-
                login fail: Please carry the API secret key in the
                'Authorization' field of the request header (1004)
              http_code: '401'
            request_id: 021785229015510a2c883cf675b9804d
    Err402:
      description: 余额/额度不足
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/OaiError'
          example:
            type: error
            error:
              type: insufficient_balance_error
              message: insufficient balance (1008)
              http_code: '402'
            request_id: 021785229015510a2c883cf675b9804d
    Err422:
      description: 输入涉及敏感内容
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/OaiError'
          example:
            type: error
            error:
              type: unprocessable_entity_error
              message: video description contains sensitive content (1026)
              http_code: '422'
            request_id: 021785229015510a2c883cf675b9804d
    Err429:
      description: 触发限流
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/OaiError'
          example:
            type: error
            error:
              type: rate_limit_error
              message: rate limit, please retry later (1002)
              http_code: '429'
            request_id: 021785229015510a2c883cf675b9804d
    Err500:
      description: 服务端错误
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/OaiError'
          example:
            type: error
            error:
              type: server_error
              message: internal error (1000)
              http_code: '500'
            request_id: 021785229015510a2c883cf675b9804d
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT
      description: |-
        `HTTP: Bearer Auth`
         - Security Scheme Type: http
         - HTTP Authorization Scheme: Bearer API_key，用于验证账户信息，可在 [账户管理>接口密钥](https://platform.minimaxi.com/user-center/basic-information/interface-key) 中查看。

````


> ## Documentation Index
> Fetch the complete documentation index at: https://platform.minimaxi.com/docs/llms.txt
> Use this file to discover all available pages before exploring further.

# 查询任务

> 按 task_id 查询最近 7 天内单个视频生成、H3-Context-IR 或视频再生成任务的状态与结果。



## OpenAPI

````yaml api-reference/video/generation/api/v2-video-generation.json GET /v2/query/video_generation/{task_id}
openapi: 3.1.0
info:
  title: MiniMax API
  description: MiniMax video generation V2 (Hailuo-03) API
  license:
    name: MIT
  version: 2.0.0
servers:
  - url: https://api.minimax.cn
security:
  - bearerAuth: []
paths:
  /v2/query/video_generation/{task_id}:
    get:
      tags:
        - Video V2
      summary: 查询任务
      description: >-
        查询单个视频生成、H3-Context-IR 或视频再生成任务的状态与结果。任务成功（`status=succeeded`）后可从
        `content` 获取产物：视频任务返回 `content.url`，H3-Context-IR 任务返回 `content.prompt`
        中的增强提示词。


        > 仅支持查询最近 7 天内的任务记录（窗口 `[T-7天, T)`，`T` 为请求发起时刻的 UTC 时间戳，精确到秒）；超出该窗口的
        `task_id` 将返回 `invalid task_id`。视频产物下载链接有时效，请及时下载或转存。
      operationId: videoGenerationV2Query
      parameters:
        - name: task_id
          in: path
          required: true
          description: 要查询的任务 ID（创建任务返回的 `task_id`）。
          schema:
            type: string
      responses:
        '200':
          description: ''
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/GetVideoGenerationV2Resp'
              examples:
                视频生成成功:
                  value:
                    task:
                      id: '424010985738629'
                      model: MiniMax-H3
                      status: succeeded
                      created_at: 1785125529
                      updated_at: 1785125946
                      content:
                        url: >-
                          https://cdn.hailuoai.com/prod/hailuo_demo/testsets/h3_promo_eval_ref2va/gallery/sr_v2p26_trio_seed42_20260724/inputs/89f8c0bbee5b_denoise_ids_0_final.mp4
                      resolution: 2K
                      duration: 5
                      usage:
                        total_seconds: 5
                        input_seconds: 0
                        output_seconds: 5
                        input_image_count: 1
                        input_audio_seconds: 6
                        total_tokens: 273890
                        prompt_tokens: 13500
                        completion_tokens: 260390
                      ratio: '16:9'
                      task_type: generation
                      modality: video
                H3-Context-IR 成功:
                  value:
                    task:
                      id: '426586401755526'
                      model: MiniMax-H3
                      status: succeeded
                      created_at: 1785702855
                      updated_at: 1785702884
                      content:
                        prompt: >-
                          integrated_multimodal_description: [Shot 1] Cinematic,
                          wide shot with a slow push in on a female captain
                          standing center frame with her back to the camera. She
                          has a slender build and short, swept-back silver hair,
                          wearing a crisp, dark navy-blue futuristic military
                          uniform adorned with rigid silver epaulets. Before her
                          stretches a colossal, curved glass observation window
                          dominating the dimly lit starship bridge. The interior
                          features sleek metallic consoles on the left and right
                          emitting soft cyan light. Outside the window, a
                          massive fleet of dark-grey, heavily armored
                          dreadnoughts and cruisers is assembling against a
                          backdrop of a swirling deep-purple and magenta nebula.
                          The rear thrusters of the distant ships glow intensely
                          with fiery orange light. [Shot 2] At 00:02.800, the
                          camera cuts to a medium close-up of the captain from
                          Shot 1 in profile facing right, while the camera
                          shakes strongly. Her facial features are now visible,
                          revealing a woman in her late forties with sharp
                          cheekbones and a stoic expression. A sudden, blinding
                          flash of brilliant cyan and white light bursts through
                          the window as the fleet outside simultaneously jumps
                          into warp, casting harsh, overexposed illumination
                          across her face. The bridge vibrates violently,
                          causing her shoulders to tense and her uniform collar
                          to tremble. The intense light instantly fades into
                          deep shadow, leaving her completely alone against the
                          newly emptied, pitch-black void of space.

                          overall_soundscape: Deep, resonant low-frequency
                          thrumming of ship engines, overlaid with rhythmic,
                          high-pitched electronic beeps from the consoles,
                          followed by a sudden, deafening sub-bass boom and a
                          loud, sizzling crackle as the warp drives engage. The
                          immense acoustic impact causes a heavy, metallic
                          clattering of the bridge panels, which instantly drops
                          off into a stark, quiet mechanical hum.

                          non_diegetic_music: Symphonic orchestral score,
                          beginning with a slow, rising brass and string
                          crescendo that abruptly cuts off, instantly
                          transitioning into a single, sustained, low-register
                          solo cello note with no dynamic swell.
                      duration: 5
                      usage:
                        total_tokens: 9090
                        prompt_tokens: 5664
                        completion_tokens: 3426
                      ratio: '16:9'
                      task_type: h3_context_ir
                      modality: text
                视频再生成成功:
                  value:
                    task:
                      id: '424010985738631'
                      model: MiniMax-H3
                      status: succeeded
                      created_at: 1785126000
                      updated_at: 1785126300
                      content:
                        url: >-
                          https://your-cdn.example.com/h3-regenerated-2k-output.mp4
                      resolution: 2K
                      duration: 5
                      usage:
                        total_seconds: 5
                        input_seconds: 0
                        output_seconds: 5
                        input_image_count: 0
                        total_tokens: 97645
                        prompt_tokens: 0
                        completion_tokens: 97645
                      ratio: ''
                      task_type: regeneration
                      modality: video
                失败:
                  value:
                    task:
                      id: '424010985738630'
                      model: MiniMax-H3
                      status: failed
                      error:
                        code: '1026'
                        message: video description contains sensitive content
                      created_at: 1785125529
                      updated_at: 1785125700
                      resolution: 2K
                      duration: 5
                      usage: {}
                      ratio: '16:9'
                      task_type: generation
                      modality: video
        '400':
          $ref: '#/components/responses/Err400'
        '401':
          $ref: '#/components/responses/Err401'
        '429':
          $ref: '#/components/responses/Err429'
        '500':
          $ref: '#/components/responses/Err500'
components:
  schemas:
    GetVideoGenerationV2Resp:
      type: object
      properties:
        task:
          $ref: '#/components/schemas/VideoTask'
      example:
        task:
          id: '424010985738629'
          model: MiniMax-H3
          status: succeeded
          created_at: 1785125529
          updated_at: 1785125946
          content:
            url: >-
              https://video-product.cdn.minimax.io/inference_output/rollout/2026-07-27/6c68f487-4b33-48cb-8c92-1631f63f6682/output.mp4
          resolution: 2K
          duration: 5
          usage:
            total_seconds: 5
            input_seconds: 0
            output_seconds: 5
            input_image_count: 1
            input_audio_seconds: 6
            total_tokens: 273890
            prompt_tokens: 13500
            completion_tokens: 260390
          ratio: '16:9'
          task_type: generation
    VideoTask:
      type: object
      description: H3 共享任务查询和列表接口返回的任务对象。
      properties:
        id:
          type: string
          description: 任务 ID。
        model:
          type: string
          description: 任务使用的模型名称，如 `MiniMax-H3`。
        status:
          type: string
          description: |-
            任务状态：
            - `queued`：排队中
            - `running`：运行中
            - `succeeded`：成功
            - `failed`：失败
            - `cancelled`：已取消
          enum:
            - queued
            - running
            - succeeded
            - failed
            - cancelled
        error:
          $ref: '#/components/schemas/VideoTaskError'
          description: 错误信息，任务成功时不返回；任务失败时返回 `code` 与 `message`。
        created_at:
          type: integer
          description: 任务创建时间的 Unix 时间戳（秒）。
        updated_at:
          type: integer
          description: 任务状态更新时间的 Unix 时间戳（秒）。
        content:
          $ref: '#/components/schemas/VideoTaskContent'
          description: 任务输出内容，任务成功后返回。
        resolution:
          type: string
          description: 任务产物的分辨率。
        duration:
          type: integer
          description: 任务产物的时长（秒）。
        usage:
          $ref: '#/components/schemas/VideoTaskUsage'
          description: 本次请求的用量。视频任务返回按秒计量的字段；H3-Context-IR 任务返回 Token 用量字段。仅任务成功时返回。
        ratio:
          type: string
          description: 任务产物的宽高比；不适用于当前任务类型时可能返回空字符串。
        task_type:
          type: string
          description: |-
            任务类型：
            - `generation`：视频生成
            - `h3_context_ir`：H3-Context-IR（`/v2/h3_context_ir`）
            - `regeneration`：视频再生成（`/v2/video_regeneration`）
          enum:
            - generation
            - h3_context_ir
            - regeneration
        modality:
          type: string
          description: 产物模态。视频生成和视频再生成任务返回 `video`；H3-Context-IR 任务返回 `text`。
          enum:
            - video
            - text
    OaiError:
      type: object
      description: OpenAI 风格错误响应。出错时 HTTP 状态码为真实错误码(401/400/429/402/422/500…),响应体为该结构。
      properties:
        type:
          type: string
          description: 固定为 `error`。
          example: error
        error:
          $ref: '#/components/schemas/OaiErrorDetail'
        request_id:
          type: string
          description: 请求追踪 ID(便于排查)。
    VideoTaskError:
      type: object
      properties:
        code:
          type: string
          description: 错误码。
        message:
          type: string
          description: 错误提示信息。
    VideoTaskContent:
      type: object
      properties:
        url:
          type: string
          description: 视频任务产物的限时下载 URL，请及时下载或转存；过期后可重新查询获取。
        prompt:
          type: string
          description: H3-Context-IR 任务生成的结构化增强提示词。仅当 `task_type=h3_context_ir` 且任务成功时返回。
    VideoTaskUsage:
      type: object
      description: 本次请求的用量。视频任务返回按秒计量的字段；H3-Context-IR 任务返回 Token 用量字段。仅任务成功时返回。
      properties:
        total_seconds:
          type: integer
          description: 本次计量总秒数 = 输入秒数 + 输出秒数。
        input_seconds:
          type: integer
          description: 输入参考视频秒数（含参考视频时计）。
        output_seconds:
          type: integer
          description: 输出视频秒数。
        input_image_count:
          type: integer
          description: 本次输入的图片总数量（first_frame + last_frame + reference_image 全部相加）。
        input_audio_seconds:
          type: integer
          description: 输入参考音频秒数（各段取和四舍五入）；无参考音频时不返回。
        total_tokens:
          type: integer
          description: |-
            本次总 Token 数 = prompt_tokens + completion_tokens。
            - 视频任务由用量折算；
            - H3-Context-IR 任务使用的 Token 总数。
        prompt_tokens:
          type: integer
          description: |-
            输入 Token 数。
            - 视频任务 = 输入参考视频秒数折算 + 全部输入图片数折算 + 输入参考音频秒数折算，无上述输入时为 0；
            - H3-Context-IR 任务的输入 Token 数。
        completion_tokens:
          type: integer
          description: |-
            输出 Token 数。
            - 视频任务 = 输出视频秒数折算；
            - H3-Context-IR 任务的输出 Token 数。
    OaiErrorDetail:
      type: object
      properties:
        type:
          type: string
          description: >-
            错误类型:`authorized_error`(401)/`bad_request_error`(400)/`rate_limit_error`(429)/`insufficient_balance_error`(402)/`unprocessable_entity_error`(422)/`overloaded_error`(529)/`server_error`(500)
            等。
        message:
          type: string
          description: 错误详情,结尾括号内为内部错误码(如 `... (1004)`)。
        http_code:
          type: string
          description: HTTP 状态码字符串,如 `401`。
  responses:
    Err400:
      description: 参数错误
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/OaiError'
          example:
            type: error
            error:
              type: bad_request_error
              message: >-
                invalid params, content must include a non-empty text item
                (prompt is required) (2013)
              http_code: '400'
            request_id: 021785229015510a2c883cf675b9804d
    Err401:
      description: 鉴权失败
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/OaiError'
          example:
            type: error
            error:
              type: authorized_error
              message: >-
                login fail: Please carry the API secret key in the
                'Authorization' field of the request header (1004)
              http_code: '401'
            request_id: 021785229015510a2c883cf675b9804d
    Err429:
      description: 触发限流
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/OaiError'
          example:
            type: error
            error:
              type: rate_limit_error
              message: rate limit, please retry later (1002)
              http_code: '429'
            request_id: 021785229015510a2c883cf675b9804d
    Err500:
      description: 服务端错误
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/OaiError'
          example:
            type: error
            error:
              type: server_error
              message: internal error (1000)
              http_code: '500'
            request_id: 021785229015510a2c883cf675b9804d
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT
      description: |-
        `HTTP: Bearer Auth`
         - Security Scheme Type: http
         - HTTP Authorization Scheme: Bearer API_key，用于验证账户信息，可在 [账户管理>接口密钥](https://platform.minimaxi.com/user-center/basic-information/interface-key) 中查看。

````