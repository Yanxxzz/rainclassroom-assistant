# RainDesk · 雨课堂自动答题程序

RainDesk 是面向**标准版雨课堂随堂习题**的 Windows 自动答题程序。老师发布题目后，程序读取题干、选项和课件图，调用所选 AI 作答。支持 GPT、DeepSeek、GLM、Claude、Gemini 等解题服务，可记住多个模型并随时切换。默认由本人逐题确认，也可以设置自动提交的缓冲时间。

[下载 Windows x64 发行版](https://github.com/Yanxxzz/rainclassroom-assistant/releases/latest)

## 使用

1. 在 Windows 10/11 x64 上运行发行包中的 RainDesk.exe，点击「扫码登录」，用手机微信扫码确认。
2. 选择进行中的课程并点击「进入并监听」。老师发题后，左侧会出现题目。切换课堂时点击「刷新」，选择新课堂后再次进入。
3. 在「设置」中填写自己的姓名和学号。主观题第一行使用这段署名。填空题每行一空，选择和判断题按原始选项编号作答。
4. 初次使用时，**「允许自动提交答案」默认关闭**。可以编辑草稿，点击「核对并提交」查看完整内容，再点击「确认提交」。
5. 勾选开关后，用旁边的「提交延时」选择 **10秒（默认）、30秒、1分钟、自定义秒数或立即提交**。每道新题从有效答案生成时独立倒计时。修改延时会计入已经等过的时间。延时超过作答期限时，界面提示可提前手动提交，题目一旦截止就取消发送。
6. 成功后点击「查看提交回执」，可查看提交方式、时间、雨课堂业务状态码和完整答案。已在微信完成的题可点「已在微信作答」。若点错，在同一位置点「撤销微信作答标记」，即可恢复草稿。题目仍开放且尚无草稿时会重新解题。核对后点「核对并提交」即可发送。若之前的提交回执不明确，撤销后先在微信核对提交记录。

缓冲期间可以编辑答案、重新解题或点「核对并提交」接手本题。以上操作会取消该题的自动发送。关闭自动提交、课堂断线或切换课堂会取消已有倒计时。重连读到的历史题和手动重解的题均留待本人核对。

### AI 的答题风格

「重新解题」旁的 **「给出解题过程」** 默认关闭。关闭时，简单题只写结果和必要单位，例如“84种”。题目明确要求证明、列式或说明理由时，保留必要论证。开启后，主观题答案附关键步骤，选择、判断、投票和填空题的步骤显示在「解析」中。设置会记住，下次生成答案时生效。要对当前草稿应用新选项，点「重新解题」。

多个独立小问按原顺序用中文分号连接，省略题号，例如“125个；60个”。题目要求简述理由时，先给结果，再点出关键一步，例如“56种。8个空隙选3个放隔板”。分类计数写清计数对象、各类数量以及如何合并。

公式用可直接复制的纯文本，如x²、(a+b)/(c+d)、C(n,k)。公式、数字与汉字之间不留空格，例如“从n个元素中选取k个”。填空只写空内内容，题面已带的单位省略。

## 从微信群窗口识别课堂码

1. 在电脑微信的聊天列表中双击课程群，将该群打开为独立窗口并保持还原。点击 RainDesk 的「监听微信群窗口」，刷新并选中这个群窗口。
2. 只想收码时直接点「开始监听」。希望自动跟课时，先登录雨课堂，在窗口中选定目标课程，勾选「发现新码时调用微信识别，并跟进所选课程」，再开始监听。该设置独立于自动提交答案。
3. 初始画面已有的码可点「处理此码」。之后单个新码会按所选模式处理，同一码在本次监听中只提醒一次。同屏出现多个新码时可自行选择。缩略图过小时，在所选窗口里放大或让微信直接识别原图。
4. 常见课堂照片内是微信通用场景码，码中没有课程名。RainDesk 会尝试调用微信图片菜单中的识码操作。若当前微信版本未提供可调用的菜单项，界面会提示你右键原图识别。若微信要求确认，请在微信完成。RainDesk 最多等候 90 秒，仅在你账号下发现匹配所选课程的唯一新课堂时接入；已选课的官方雨课堂链接也可按课堂 ID 匹配。收到课堂连接事件后显示「已进入课堂并开始收题」。

窗口采集使用 Windows Graphics Capture，要求 Windows 10 1903 或更新版本。遮住群窗口仍可监听，最小化或隐藏时暂停，关闭或更换窗口时停止。在电脑上关闭 RainDesk 即结束监听。群窗口画面只在本机内存中识别，截图、群名称和二维码内容均不落盘。

## 解题服务

在「设置 → 解题服务」选择服务，填写或导入该服务的密钥，选择模型后点「保存设置」。同一服务的模型共用该服务的密钥，只需配置一次。要添加同一服务的第二个模型，输入新的模型 ID 并保存。以后在主窗口点 **「切换模型」** 就能直接选用，新题和手动重新解题会使用新选择。程序重启后会恢复上次的模型、各服务的密钥和程序路径。

密钥可直接粘贴，也可从单行 `.txt` 或 `.env` 文件导入。一个 `.env` 里可以放多家的密钥，导入时只读取所选服务对应的变量。清空某家密钥并保存即可移除该密钥。

| 解题服务 | 初次配置 |
| --- | --- |
| GPT | 默认使用本机已登录 ChatGPT 的 Codex CLI，支持默认模型及自定义模型 ID。[Codex 文档](https://developers.openai.com/codex/noninteractive/) |
| OpenAI API | 使用独立的 `OPENAI_API_KEY`。[OpenAI API 文档](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) |
| DeepSeek Harness | 导入 `DEEPSEEK_API_KEY` 并留空程序路径即可使用官方 API。选择本机 Harness 路径可沿用其配置，选择「服务默认」会保留 Harness 的模型路由。Harness 模式使用题面文字。[官方 API](https://api-docs.deepseek.com/) · [Harness 配置](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/cli-help.md) |
| WorkBuddy | 选择本机程序或已登录的 CodeBuddy CLI，可填写模型别名。[官方 CLI 文档](https://www.workbuddy.cn/docs/cli/cli-reference) |
| GLM | 智谱开放平台的 `ZHIPUAI_API_KEY`。[智谱文档](https://docs.bigmodel.cn/cn/api/introduction) |
| Claude | Anthropic 的 `ANTHROPIC_API_KEY`。[Claude Messages 文档](https://platform.claude.com/docs/en/api/messages/create) |
| Gemini | Google AI Studio 的 `GEMINI_API_KEY` 或 `GOOGLE_API_KEY`。[Gemini 文档](https://ai.google.dev/api/generate-content) |
| 通义千问 | 阿里云百炼中国内地的 `DASHSCOPE_API_KEY`。[百炼文档](https://help.aliyun.com/zh/model-studio/qwen-vl-compatible-with-openai) |
| Kimi | 月之暗面国内平台的 `MOONSHOT_API_KEY`。[Kimi 模型列表](https://platform.kimi.com/docs/models) |
| 豆包 | 火山方舟北京地域的 `ARK_API_KEY`，填写已开通的模型或接入点 ID。[方舟文档](https://www.volcengine.com/docs/82379/1494384) |
| Grok | xAI 的 `XAI_API_KEY`，填写账户可用的模型 ID。[xAI 文档](https://docs.x.ai/developers/model-capabilities/legacy/chat-completions) |
| OpenRouter | `OPENROUTER_API_KEY` 和所选模型的完整 ID。[OpenRouter 文档](https://openrouter.ai/docs/api/reference/overview) |
| 硅基流动 | 国内平台的 `SILICONFLOW_API_KEY` 和完整模型 ID。[硅基流动文档](https://docs.siliconflow.cn/cn/api-reference/chat-completions/chat-completions) |

云端 API 会接收当前题目的文字及可用的题面图片。含图题请选择该平台支持图片输入的模型。

## 数据与回执

扫码由微信和雨课堂处理。账号登录态、个人设置和最近 500 条提交状态使用 Windows DPAPI 加密，保存在当前 Windows 用户的 %LOCALAPPDATA%\RainDesk 下。AI 接收题面、选项和当前页素材。主观题署名由程序在本地组装最终答案时添加。

连接中断后会重连并读取课堂时间线。业务码为 0 的响应会记录为已提交。若发送后连接中断或回执缺失，显示「提交待核对」，请先在微信里查看本题的作答记录。分数和教师批改由雨课堂显示。只检测随堂课件中的发题，普通翻页可继续听课。

## 从源码运行

安装 Windows x64 Python 3.12 后，在项目目录运行以下命令。

    py -3.12 -m pip install -r requirements.txt
    py -3.12 RainDesk.py

执行 build.ps1 可在隔离环境中安装依赖、运行测试并生成 dist/RainDesk.exe。单独运行测试可使用 py -3.12 -m unittest discover -s tests。

雨课堂客户端的互通行为参考了 [AneryCoft/course_helper](https://github.com/AneryCoft/course_helper)。本项目为独立的 Python Windows 客户端。
