# 雨课堂答题助手

Windows 桌面客户端可监听标准版雨课堂的课堂发题，自动读取当前题面、生成可编辑的答案。核对弹窗会显示课程、题号和实际发送的内容，点击「确认提交」后由当前账号提交。

## 使用

1. 在 Windows 10/11 x64 上运行发行包中的 RainDesk.exe，点击「扫码登录」，用手机微信扫码确认。
2. 选择进行中的课程并点击「进入并监听」。老师发题后，左侧会出现题目。切换课堂时点击「刷新」，选择新课堂后再次进入。
3. 在「设置」中填写自己的姓名和学号。主观题第一行使用这段署名。填空题每行一空，选择和判断题按原始选项编号作答。
4. 检查题目、课件图和答案，按需编辑。多小问主观题按原顺序用中文分号连接，简单题给出结果和必要单位，要求过程的题保留关键步骤。
5. 点击「核对并提交」预览，点击「确认提交」发送。成功后点击「查看提交回执」可查看时间、业务状态码和本次送出的完整答案。已在微信完成的题可标记「已在微信作答」。

## 解题服务

默认使用本机已登录 ChatGPT 的 Codex CLI。需要能运行 codex exec 的近期版本。CLI 必须可从 PATH 找到，模型留空时使用其默认 GPT。账号按本机登录状态计量使用。

也可在「设置」切换到 WorkBuddy 或 DeepSeek Harness，选择相应桌面程序或厂商 CLI。三个服务使用同一份作答要求。WorkBuddy 通过 CodeBuddy 的无头模式接收题目文字和图片。DeepSeek Harness 的 headless 接口接收题干、选项和同页课件文字，依赖图片的题建议使用 GPT 或支持视觉的 WorkBuddy 模型。每种服务需要在自己的客户端或 CLI 完成身份配置。

[OpenAI 非交互调用文档](https://learn.chatgpt.com/docs/non-interactive-mode) · [WorkBuddy CLI 文档](https://www.workbuddy.cn/docs/cli/headless) · [DeepSeek Harness CLI 文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/cli-help.md)

## 数据与回执

微信登录仅在雨课堂与微信之间进行。账号登录态、设置和最近 500 条提交状态由 Windows DPAPI 加密保存在当前系统用户的 %LOCALAPPDATA%\RainDesk 下。AI 接收老师发布的题面、选项和当前页的必要素材。主观题署名在本地提交预览时添加。

连接中断后会重连并读取课堂时间线。业务码为 0 的响应会记录为已提交。若发送后连接中断或回执缺失，显示「提交待核对」，请先在微信里查看本题的作答记录。分数和教师批改由雨课堂显示。只检测随堂课件中的发题，普通翻页可继续听课。

## 从源码运行

安装 Windows x64 Python 3.12 后，在项目目录运行以下命令。

    py -3.12 -m pip install -r requirements.txt
    py -3.12 RainDesk.py

执行 build.ps1 可在隔离环境中安装依赖、运行测试并生成 dist/RainDesk.exe。单独运行测试可使用 py -3.12 -m unittest discover -s tests。

雨课堂客户端的互通行为参考了 [AneryCoft/course_helper](https://github.com/AneryCoft/course_helper)。本项目为独立的 Python Windows 客户端。
