# 首次配置与故障恢复

依赖确认保存在本机技能状态中。普通调用只读取缓存，不访问 Spotify、不执行 ncm-cli 探测，也不要求用户重复确认配置。

## 缓存门禁

运行 `W setup-status` 读取 `spotify` 和 `ncm` 的 `confirmed` 标记。这只是本地 SQLite 读取，不验证外部服务：

- Spotify 已确认，且本次明确只推荐：直接进入正式流程。
- Spotify 和 ncm 均已确认：默认推荐并保存任务直接进入正式流程。
- 所需标记未确认：只为缺失的组件执行下方首次配置，不重检已确认组件。

配置成功后不会设置有效期。不要为了“以防万一”在每次任务中检查工具清单、调用 Spotify 探路，或运行 `doctor`。只有实际依赖错误、用户明确要求重检，或用户说明更换了账户／API 配置时才进入故障恢复。

## 首次确认 Spotify

检查当前会话的可用工具，确认存在由 `Spotify` 插件提供、支持搜索／推荐的可调用工具。不要把本机 Spotify 桌面客户端、网页访问能力或模型记忆当成插件。

工具已存在时运行 `W setup-confirm --component spotify`，以后不再做安装检查。工具不存在时：

1. 说明本技能依赖 Spotify 插件，当前会话无法正式开始推荐。
2. ChatGPT 网页端或 ChatGPT 桌面应用中的 Codex：打开“插件”，搜索 `Spotify`，打开详情并选择加号安装；按提示连接 Spotify 账户。
3. Codex CLI：输入 `/plugins`，搜索并安装／启用 `Spotify`。
4. 安装或连接完成后开启新对话或新 CLI 会话，再次调用 `$spotify-genre-recommender`；新会话确认工具存在后记录标记。

IDE 扩展不支持插件；引导用户改用 ChatGPT 桌面应用中的 Codex 或 Codex CLI。若插件未对当前账户或工作空间开放，说明限制并停止，不用网页搜索或模型记忆替代插件结果。

## 首次确认 ncm-cli

默认会保存网易云，因此 ncm 尚未确认时运行 `W doctor --remember`。它检查 ncm-cli 可执行文件、AppId、PrivateKey 和网易云登录，仅在全部 ready 时记录确认。只有用户在本次请求中明确“仅推荐／不保存”时才跳过；以后首次需要保存时再完成。

未 ready 时按返回的 `status` 处理：

- `missing`：说明需要 Node.js 18+，引导用户运行返回的安装和版本验证命令。若环境已有 `$ncm-cli-setup`，在用户同意开始配置后调用；否则直接展示 doctor 的步骤。
- `configuration_required`：给出返回的网易云开放平台申请地址和缺失字段。让用户在自己的终端设置 AppId 和 PrivateKey；不得要求用户把 PrivateKey 粘贴到聊天、日志或可提交文件中。
- `login_required`：引导运行 `ncm-cli login --background`，按终端提示完成登录，再用 `ncm-cli login --check` 检查。
- `unavailable`：报告 ncm-cli 无法执行，检查 PATH、版本和安装状态；不要反复重试同一失败命令。

用户完成配置后重新运行 `W doctor --remember`。当前技能不使用播放功能，因此不要安装 mpv 或设置播放器。

## 故障恢复与主动重检

- Spotify 工具实际不存在或返回认证错误：保留批次，运行 `W setup-reset --component spotify`，再按首次确认 Spotify 处理。
- ncm 操作失败且疑似安装、配置或登录问题：先运行一次 `W doctor`。若不是 ready，再运行 `W setup-reset --component ncm` 并按对应状态引导；doctor 仍为 ready 时按普通服务错误处理，不清除确认。
- 用户更换 Spotify／网易云账户、AppId、PrivateKey，或明确要求重新检查：用 `W setup-reset --component spotify|ncm|all` 清除相应标记，再执行一次首次确认。

安装插件或 CLI、连接账户、写入配置和登录都会改变外部或本机状态。先说明将执行的操作并取得用户同意，或让用户自行操作。插件界面和按钮可能随产品更新；遇到差异时以 [OpenAI 插件文档](https://learn.chatgpt.com/zh-Hans/docs/plugins) 为准。
