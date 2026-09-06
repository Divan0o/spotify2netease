# Spotify → NetEase

一个面向 Codex 和 ChatGPT 的个人 skill：使用 Spotify 按指定风格推荐歌曲，跨批次去重，并通过 `ncm-cli` 将匹配结果保存到网易云音乐歌单。

## 功能

- 按音乐风格和数量获取 Spotify 单曲推荐
- 逐曲审查风格与艺人身份，避免只按标题误判
- 使用 SQLite 保存批次、候选、去重历史和恢复状态
- 将选中曲目匹配到网易云音乐并创建歌单
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
- 已安装并配置可用的 Spotify 插件
- 已安装、登录并可正常使用 `ncm-cli`
- macOS 或 Linux（保存流程使用文件锁）

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
