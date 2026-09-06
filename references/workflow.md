# 批次命令与恢复

以下 `W` 表示 `python3 <skill-dir>/scripts/workflow.py`，其中 `<skill-dir>` 是本技能的安装目录；`W` 不是实际命令名。所有全局 `--root` 放在子命令前。输入 JSON 使用已有文件或工具安全写入的文件重定向，不将歌曲文本拼进 shell 代码。

```text
W doctor
W new --genre "baile funk" --count 10
W plan --genre "baile funk"
W plan --batch ID
W reuse --batch ID --from-batch SOURCE_ID < cached-selection.json
W batches
W status --batch ID
W ingest --batch ID --query "实际查询" [--artist-uri 返回的艺人URI] < response.json
W pool --batch ID
W assess --batch ID < reviews.json
W select --batch ID [--diversity-reason "具体理由"] < selection.json
W queries --batch ID
W search --batch ID
W prepare --batch ID
W export --batch ID
W render --batch ID
```

`doctor` 是无状态、只读预检，不创建状态目录或批次；它检查 ncm-cli 可执行文件、AppId、PrivateKey 和网易云登录。正式流程开始前以及用户完成配置后调用；状态不是 ready 时按[启动前依赖预检](setup.md)引导，不进入搜索或写入。

`new` 默认 `--mode recommend`；只有用户明确要求网易云存满数量才用 `--mode save`。用户只要推荐时不调用 search/export。测试先用 mktemp -d 建立独立目录，再 `W --root 该目录 new --genre ... --count ... --test`；export 对测试批次拒绝写入。

`plan --genre` 只读已有批次，返回同风格未推荐缓存、最多8个已验证主艺人线索及历史低产查询，不产生网络请求或推荐预留。`plan --batch` 另外给出缺额、可选URI、有限待审窗口及下一步select/review/reuse/discover/save_or_deliver；只是提示，不自动放宽证据或去重。未带artist-uri的旧查询仍须看原query，不能据此判为从未查过。

`entry_route` 有可用缓存时为 `reuse_cache`；无缓存但有已验证艺人线索时为 `artist_track_suffix`，建议 `艺人名 {track}`；没有艺人线索时为 `genre_track_suffix`，使用 `风格 {track}` 冷启动。风格后缀始终是允许的发现方式，标题匹配照常入池，`title_hits` 不作为自动拒绝条件。该字段只提示需要发现时的检索方式；有批次时先执行 `action` 指定的审查、选中或交付步骤。指定作品可直接精确查找，经审查无有效产出的分支再回退。首轮模板和工具契约边界见[首轮检索规则](genre-review.md#首轮检索规则)。

`reuse`输入 `{"uris":["plan返回的真实URI"]}`，按source_batch分组调用；事务内重新排除推荐历史、possible冲突、不同风格、测试／取消源、身份错配和uncertain/rejected候选。只复制曲目、评估与来源链，不复制网易云匹配、不占Spotify调用、不预留历史；之后用plan/assess/select继续。此操作不是重新联网获取，不能把缓存描述为本次新检索。缓存已足够时无需为了“用插件”再做无效网络请求。

status/assess/select 默认仅输出摘要，不重复整份查询日志；search 默认隐藏已匹配曲目的候选和搜索详情，仅保留待人工判定项的候选。需要排查时加 --verbose；queries/pool/prepare 可显式读取细节，避免搜索已经结束还反复打印全部结果。

`ingest` 输入完整 Spotify 工具 JSON 或其 structuredContent，不手工编造实体。`assess` 输入数组：

```json
[{"uri":"插件返回URI","status":"accepted","basis":"对该曲的具体风格判断","evidence":[{"kind":"external","scope":"track","url":"真实核验页面的https链接","claim":"页面实际支持的事实"}]}]
```

也支持可靠既有知识证据 `{"kind":"knowledge","scope":"track","certainty":"known","claim":"该具体作品的已知风格依据"}`。示例不是可直接提交的数据，必须填真实依据。不确定时使用 uncertain。

`select` 输入 `{"uris":["真实URI"],"distinct_reasons":{}}`。可分批选中；每次原子复核全局历史和本批录音重复。可能同录音需 distinct_reasons 以 URI 为键填写真实不同版本证据。保存数模式在 search/resolve 后选中；select 会拒绝相同网易云 ID。

## 状态存储

默认 `~/.codex/state/spotify-genre-recommender/workflow.sqlite3` 为唯一写入源，使用 SQLite 事务同时保存历史预留与批次。首次从 recommended.json v1 导入；原文件原样保留，不再双写。旧 exports 保留供查阅；新批次全部在数据库中恢复。旧历史缺元数据时采取保守 possible 去重，不无依据补元数据。

`history.py summary` 看概况；filter 输入候选数组并返回冲突原因。旧裸 reserve 已移除，防止写入历史却丢失批次。不要直接编辑数据库，不回退旧脚本继续写 recommended.json。

selected 是已预留，ready 是清单已准备且可恢复，delivered 是确认交付，saved 按网易云读回逐曲记录。render 不证明用户已收到；随后收到用户对该清单的明确回应等确认后才用 `mark-delivered --batch ID`，无需额外打扰用户。所有活跃阶段都避免跨批重推；保存失败不删除推荐预留。`abandon` 仅用于用户明确取消、且未 ready／交付／产生外部副作用的批次，不能自动过期释放。

恢复时先 status，沿用选中清单和匹配缓存；已预留曲目属于原批次，不再次被自身历史过滤。不要盲目新开一批或重建歌单。若创建结果不明且无法唯一定位，报告需用户确认，不扩大权限删除歌单或重试创建。
