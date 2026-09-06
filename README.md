# Spotify → NetEase

一个面向 Codex 和 ChatGPT 的个人 skill：使用 Spotify 按指定风格推荐歌曲，跨批次去重，并通过 `ncm-cli` 将匹配结果保存到网易云音乐歌单。

## 功能

- 按音乐风格和数量获取 Spotify 单曲推荐
- 逐曲审查风格与艺人身份，避免只按标题误判
- 使用 SQLite 保存批次、候选、去重历史和恢复状态
- 将选中曲目匹配到网易云音乐并创建歌单
- 正式运行前检测 Spotify 插件；需要保存时同时检测 `ncm-cli`、API 凭据和登录状态
- 支持中断恢复、写入后对账和隔离测试

## 安装

使用 Codex 内置的 `$skill-installer` 从本仓库安装：

```text
$skill-installer install https://github.com/Divan0o/spotify2netease
```

也可以手动克隆到 Codex skills 目录：

```bash
git clone https://github.com/Divan0o/spotify2netease.git ~/.codex/skills/spotify-genre-recommender
```

安装或更新后，重新打开 Codex 会话以加载 skill。

## 前置条件

- Python 3
- Spotify 插件；未安装或未连接时 skill 会先给出安装和连接引导
- 保存到网易云需要 Node.js 18+ 和 `ncm-cli`；未安装或未配置时 skill 会给出引导
- macOS 或 Linux（保存流程使用文件锁）

## 启动前依赖引导

skill 正式运行前会先确认当前会话存在由 `Spotify` 插件提供的搜索工具。若不存在：

- ChatGPT 网页端或 ChatGPT 桌面应用中的 Codex：打开“插件”，搜索并安装 `Spotify`，按提示连接账户。
- Codex CLI：输入 `/plugins`，搜索并安装／启用 `Spotify`。
- 安装后开启新对话或新 CLI 会话，再次调用本 skill。
- IDE 扩展不支持插件，请改用桌面端 Codex 或 Codex CLI。

详细安装方式以 [OpenAI 官方插件文档](https://learn.chatgpt.com/zh-Hans/docs/plugins) 为准。本机安装了 Spotify 客户端并不代表插件已经可用。

默认需要保存网易云，因此 Spotify 检查通过后还会运行只读的 ncm-cli 预检：

```bash
python3 scripts/workflow.py doctor
```

预检会区分 `missing`、`configuration_required`、`login_required`、`unavailable` 和 `ready`。未就绪时，skill 会根据状态引导安装、申请并设置 AppId/PrivateKey，或完成网易云登录；配置值和账户 ID 不会写入预检结果。本次明确只推荐、不保存时会跳过 ncm-cli，但不会跳过 Spotify 插件检查。

PrivateKey 应由用户在自己的终端中设置，不要粘贴到聊天、日志或仓库。配置完成后再次运行 doctor，只有返回 `"ready": true` 才会继续网易云搜索和歌单写入。本 skill 不使用播放功能，因此无需安装 mpv。

## 使用

```text
$spotify-genre-recommender 推荐 10 首 baile funk，并保存到网易云
```

不想保存时明确说明：

```text
$spotify-genre-recommender 推荐 15 首 footwork，只推荐，不保存
```

## 测试

```bash
python3 -m unittest discover -s tests -v
```

详细工作流和约束见 [SKILL.md](SKILL.md)。
