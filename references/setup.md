# 启动前依赖预检

在询问风格和数量、创建或恢复批次、调用 Spotify 或写入网易云之前完成本预检。不要把本机 Spotify 桌面客户端、网页访问能力或模型记忆当成 Spotify 插件已安装。

## Spotify 插件

先检查当前会话的可用工具，确认存在由 `Spotify` 插件提供、可调用的 Spotify 搜索工具。工具名称可能因宿主版本变化，不依赖单一内部名称；以工具元数据明确属于 Spotify 且支持搜索／推荐为准。

若工具不存在：

1. 说明本技能依赖 Spotify 插件，当前会话无法正式开始推荐。
2. ChatGPT 网页端或 ChatGPT 桌面应用中的 Codex：打开“插件”，搜索 `Spotify`，打开详情并选择加号安装；按提示连接 Spotify 账户。
3. Codex CLI：输入 `/plugins`，搜索并安装／启用 `Spotify`。
4. 安装或连接完成后开启新对话或新 CLI 会话，再次调用 `$spotify-genre-recommender`。不要在旧会话中声称插件已经可用。

IDE 扩展不支持插件；引导用户改用 ChatGPT 桌面应用中的 Codex 或 Codex CLI。若插件未对当前账户或工作空间开放，说明限制并停止，不用网页搜索或模型记忆替代插件结果。

工具存在但首次调用返回身份验证错误时，原样说明 Spotify 返回的认证错误，引导完成插件连接；连接后重新检查工具可用性再继续。不要声称安装或连接成功，除非当前会话中已看到工具或实际调用得到有效响应。

## ncm-cli

默认会保存网易云，因此在正式推荐前运行 `W doctor`。只有用户在本次请求中明确“仅推荐／不保存”时才跳过 ncm-cli 预检。已有批次恢复时仍先预检，但不能因未就绪而丢弃、重开或改写批次。

按返回的 `status` 处理：

- `ready`：依赖已就绪，可以进入正式流程。
- `missing`：说明需要 Node.js 18+，引导用户运行返回的安装和版本验证命令。若环境已有 `$ncm-cli-setup`，在用户同意开始配置后调用；否则直接展示 doctor 的步骤。
- `configuration_required`：给出返回的网易云开放平台申请地址和缺失字段。让用户在自己的终端设置 AppId 和 PrivateKey；不得要求用户把 PrivateKey 粘贴到聊天、日志或可提交文件中。
- `login_required`：引导运行 `ncm-cli login --background`，按终端提示完成登录，再用 `ncm-cli login --check` 检查。
- `unavailable`：报告 ncm-cli 无法执行，检查 PATH、版本和安装状态；不要反复重试同一失败命令。

安装插件或 CLI、连接账户、写入配置和登录都会改变外部或本机状态。先说明将执行的操作并取得用户同意，或让用户自行操作。当前技能不使用播放功能，因此不要安装 mpv 或设置播放器。配置完成后重新运行完整预检；Spotify 工具可用，且需要保存时 `doctor` 返回 `ready: true`，才进入正式流程。

插件的界面和按钮可能随产品更新；遇到差异时以 [OpenAI 插件文档](https://learn.chatgpt.com/zh-Hans/docs/plugins) 为准。
