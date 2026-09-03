# 群聊定时摘要（Group Digest）

## 一句话说明

按 cron 调度定时拉取一个**分组**内所有会话的消息，打包成一次 AI 调用生成按会话分段的摘要，写入通知队列并推送到所有已绑定渠道，同时滚动更新该分组的记忆。

## 数据流

```
用户在前端配置摘要分组（组名 / 多个会话 / 时间 / 档案 / 推送）
    │
    ▼
AssistantConfig.digest_groups[] → data/assistant_config.json
    │
    ▼
DigestScheduler daemon 线程（60s 轮询）
    │  _should_trigger() → cron 或 HH:MM 匹配
    │  防重触 key = dg:{group_id}，MIN_TRIGGER_GAP_SEC=120s
    ▼
_run_digest_in_pool(group_id)          ← 传 id 不传对象，运行时从磁盘重读
    ▼
_generate_digest(dg)
    1/6 _collect_chat_units()   逐会话取消息（limit=500，超出丢**最旧**、返回仍升序）
                                → unread_only 按会话各自切尾部（未读数一次拉全表）
                                → filter_messages()（噪音 + 媒体占位 + XML 清洗）
    2/6 plan_digest()           估算 token → 判定 single / packed / per_chat
    3/6 _render_digest()        ├─ single   → 1 次 LLM，prompt 与改造前逐字节一致
                                ├─ packed   → 1 次 LLM，多会话分节 + 输出契约
                                │             被 provider 拒（上下文超限）→ 就地转 per_chat
                                └─ per_chat → N 次 LLM（并发 3）+ concat_sections 纯拼接
                                              **不再有第 N+1 次汇总调用**
    4/6 _update_group_memory()  memory_enabled? → 组级单一记忆，按 group_id 写回磁盘
                                （全部会话失败时跳过，不把错误串浓缩进记忆）
    5/6 _publish_outbox()       chat_id=组 id，content 带 chats/digest_mode/degraded/dropped_total
    6/6 _push_and_finish()      推给**所有**已扫码绑定渠道（legacy push_target 只是旧 opt-in 标记）
                                → 三态状态走 delivery.aggregate_status → 任务收尾
```

## 配置字段

### DigestGroup（`src/assistant/config.py`）

一条 `DigestGroup` = **一个分组**（不再是"一个会话"），与 `OAGroup` 同构。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `id` | str | "" | 分组唯一 id，`dg_001` 式，由 `_next_digest_group_id` 分配 |
| `name` | str | "" | 分组显示名 |
| `chats` | list[DigestChat] | [] | 组内会话；**一个会话只能属于一个分组** |
| `schedule` | list[str] | [] | HH:MM 触发时间列表（遗留字段，与 cron_expr 互斥，cron 优先） |
| `cron_expr` | str | "" | 5 字段 cron 表达式（多行，每行一个触发时间） |
| `lookback_hours` | int | 6 | 回溯窗口（3/6/12/24） |
| `lookback_mode` | str | "manual" | `"auto"`=前端按 cron 间隔智能推算 / `"manual"`=用 lookback_hours |
| `enabled` | bool | True | 分组主开关 |
| `profile` | GroupProfile? | None | 群档案（输出风格配置），组级共用 |
| `memory` | str | "" | **组级单一**累积摘要记忆，按 `### 会话名` 分块；字数上限随会话数放大（见「记忆更新」） |
| `memory_rev` | int | 0 | 记忆版本号，后台每次写入 +1，手工编辑走 CAS |
| `memory_enabled` | bool | True | 记忆开关：关闭后摘要不再更新记忆 |
| `unread_only` | bool | False | 仅摘要未读消息（按会话各自切尾部） |
| `push_target` | str | "" | "ilink"=推到微信，""=仅入队（遗留开关，实际推所有已绑定渠道） |

> `lookback_mode` 此前只存在于前端（`AssistantPanel.jsx` 的智能回溯按钮一直在发这个字段），
> 后端 `DigestGroup` 没有它，所以"智能回溯"选了也从来没被持久化过 —— 现已补上。

### DigestChat（嵌套）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `chat_id` | str | "" | 会话 ID，必填、全配置内唯一 |
| `name` | str | "" | 保存时的显示名快照，用作摘要分段标题与推送展示 |
| `enabled` | bool | True | 会话级开关：分组开着但这个会话本轮不摘要 |

### 旧数据迁移（`_parse_digest_groups`）

旧的"一个会话一条配置"（`chat_id` + `group_name`）在加载时 1:1 迁移成单会话分组：

- 自动分配 `dg_NNN` 形式的 `id`，`name` 取旧 `group_name`
- `chats` = 单个 `DigestChat(chat_id=旧 chat_id, name=旧 group_name)`
- `memory` **逐字符原样带过去**（线上 3 条累计 4408 字符，是 LLM 逐次累积、无法重新生成的数据）
- `memory_rev` 从 0 起
- `chat_id` 为空的旧组（agent 工具建的）→ `chats=[]` + warning 提示去网页端重新绑定
- 跨组重复的 `chat_id` 保留第一个

迁移是**纯函数且幂等**，`_config_to_dict → _dict_to_config` 往返为恒等映射；且**绝不抛异常**
（脏值走 `_safe_int` / `str(x or "")` 回落默认），因为 `load_assistant_config` 的 except 分支
会用默认配置覆盖整份文件。落盘后不再输出 `chat_id` / `group_name` 顶层键。

### GroupProfile（嵌套）

> summary（群简介）/ focus（关注点）/ ignore（忽略内容）三个字段已移除：
> 特殊需求直接写在 `custom_prompt` 中。

| 字段 | 类型 | 说明 |
|------|------|------|
| `style` | str | 摘要风格预设："" / "行动项优先" / "完整复盘" / "极简速览" / "自定义" |
| `custom_prompt` | str | 自定义摘要指令 |

## Prompt 架构

### system_prompt = 风格层 + 结构层

```
风格层（可被 custom_prompt 完全替代）
  有 custom_prompt? → 用 custom_prompt
  否则              → DIGEST_SYSTEM_PROMPT + style 预设（行动项优先 / 完整复盘 / 极简速览）

结构层（按 plan.mode 追加，custom_prompt 覆盖不掉）
  single   → SINGLE_HEADING_CONTRACT   第一行 `## 会话名`，会话内只用 ### 或 -
  packed   → PACKED_OUTPUT_CONTRACT    N 个会话各一段，禁止跨会话合并/总览
  per_chat → PER_CHAT_NO_HEADING_HINT  禁止任何 # 标题，外层 concat_sections 自己加会话名
```

- **风格层可替代**：用户对摘要写法有完全控制权，仅影响该分组。
- **结构层不可替代**：标题层级是外层切分与归属的依据，被 custom_prompt 覆盖掉就没法把正文按会话拆开。

`DIGEST_SYSTEM_PROMPT` 本身也按分组口径重写：明说"一个分组里可能 1 个也可能多个互不相干的会话"、钉死标题层级（会话 `##`、会话内话题 `###` 或 `-`）、并警告近期记忆是全组共用的、不要张冠李戴。

**为什么要钉层级**：旧默认 prompt 写的是"按话题分类，每个话题用 `##` 二级标题"，与打包契约要求的 `## 会话名` 直接冲突。实测 6 个会话走降级路径时模型选了前者，正文炸出 **24 个 h2**，`## 攒单群` 后面跟着 `## 淘宝攒单新规`、`## 攒单策略讨论`，与 `concat_sections` 加的会话名同级 —— 读的人分不清哪个话题属于哪个群。

### user_prompt 结构（`build_digest_prompt()`）

```
## 本次摘要的会话
{会话名}

## 近期记忆（分组「{组名}」共用，可能含其他会话的事，只在与「{会话名}」相关时引用）
{memory 或 "（暂无历史记忆）"}

## 「{会话名}」最近 N 条消息
[HH:MM] 昵称: 内容
...
```

打包路径用 `build_packed_prompt()`，开头是 `## 本次摘要的分组\n{组名}（N 个会话）`，记忆段同样标注"本分组共用"，消息按 `=== [i] 会话名 (条数) ===` 分节。

只提供上下文，指令全部在 system_prompt 侧。**会话名必须出现在 user prompt 里**：single 与 per_chat 两条路径都只给一个会话的消息、却给整组的记忆，不点名会话，模型无从判断记忆里哪些内容与当前会话有关。

## 消息过滤

`filter_messages()` 处理四类问题：

1. **噪音过滤**：系统消息（入群/退群/改群名）、纯表情、极短消息、常见无意义回复（"收到"/"好的"/"ok" 等）。
2. **媒体占位**：图片/语音/视频/贴纸/应用消息等非文本类型，原始内容替换为结构化占位符（`{{ image }}` / `{{ voice }}` 等），让 LLM 知道上下文而不接触原始载荷。
3. **伪装成文本的 XML**（`_clean_xml_content`）：WCDB 把群接龙、引用回复、名片、图片、表情、语音、视频都存成 `msg_type=1`，content 是完整 XML 载荷。只按 `msg_type` 判断的 `MEDIA_RAW_TYPES` 完全漏掉这些，原始 XML 会直接进 prompt（实测单条最长 **17,015** 字符）。因此改为**按 XML 根元素结构分类**：
   - 有 `<title>` → `{{app: 标题}}`（接龙/引用/卡片分享，取外层 title 而非 `<refermsg>` 里被引用的文本）
   - `<pushmail>` 的 `<subject>` → `{{mail: 主题}}`（这是真实文本，不能丢）
   - `<img>` / `<emoji>` / `<videomsg>` / `<voicemsg>` / `<location>` → 复用 `MEDIA_PLACEHOLDERS` 的对应标签，避免 LLM 看到两套词汇
   - 名片（`<msg bigheadimgurl=...>`）→ `{{ contact_card }}`
   - 其余无法识别 → `{{app_message}}`

   `XML_MIN_LEN = 60` + 必须以 `<` 开头 + 起始标签白名单三重判定，保证 `"<3 你"`、`"a<b"` 这类普通短文本不被误伤。生产库 72h 实测：4793 条长消息中 **2632 条（55%）**是这类 XML，共 7,265,605 字符，折叠后剩 **45,659**（-99.4%），其中仅 38 条落到无信息的 `{{app_message}}`。
4. **标识符清洗**：消息文本中的 `wxid_xxx` / `gh_xxx` 等内部标识符在进入 prompt 前被剥离，保证摘要只展示昵称。

> 这些处理同时服务于群摘要与关键词提醒（共用 `_strip_ids`），保证 LLM 输入与匹配/展示一致。

## Token 预算与降级

一个分组内所有会话打包成**一次** LLM 调用；打包超出预算时降级为逐会话摘要，然后**纯字符串拼接**。

### 预算口径

`DIGEST_TOKEN_BUDGET`（env，默认 **150_000**，夹到 `[10_000, 400_000]`）。

口径**复用** `AbstractSummarizer._estimate_tokens`（`src/summarize/base.py`：1.5 字符/token + 每消息 40 字符开销 + 500 固定），不自己引 tokenizer：

- 实测这批语料真实比例是 **0.42 token/字符**，估算器给 0.667 —— **保守约 1.6 倍**，保守正是需要的方向
- 天花板设 400K 是因为估算 400K ≈ 真实 250K token，正好是实测的 provider 拒绝线（MiniMax-M2.7-highspeed：192K/224K 真实 token 通过、~250K 拒）
- **不改** `_estimate_tokens` 的公式，也**不动** `token_budget` 类属性（`src/web/ai_chat.py` 拿它算上下文压缩阈值）

**kill switch**：`DIGEST_TOKEN_BUDGET=1` → 被夹到下限 10_000 → 任何多会话分组都必然超限从而走 per_chat。出问题时改 `.env` 重启即可，不需要回滚代码。

### 四个分支（`plan_digest`）

`available = budget − memory_tokens − SYSTEM_RESERVE_TOKENS(800)`，且不低于 `MIN_AVAILABLE_TOKENS(2000)`。

| 分支 | 条件 | 行为 |
|------|------|------|
| `single` | 有内容的会话只有 1 个 | 1 次调用，**不加分节标记、不追加输出契约**，prompt 与改造前逐字节一致 |
| `packed` | `total ≤ available` | 1 次调用，多会话分节 |
| `packed`（裁剪后） | 超限 ≤ `PACKED_TRIM_SLACK_RATIO(0.20)` | 按各会话占比均摊裁最旧，仍 1 次调用 |
| `per_chat` | 超限 > 20%，或均摊后被 `MIN_UNIT_TOKENS(200)` 地板顶住仍超限 | N 次调用（并发 `DIGEST_PER_CHAT_WORKERS=3`）+ 纯拼接 |

`total = Σ 各会话 est_tokens + SECTION_OVERHEAD_TOKENS(40) × 会话数`。

> `single` 分支的意义：迁移后线上 3 个分组都是单会话，**升级当天行为零变化**。

### 裁剪规则

- **裁最旧、留最新**（`trim_oldest` 二分求最小起点），返回值仍按时间升序
- 预算小到连一条都装不下时**至少保留最新 1 条**，绝不返回空
- 省略必须在两处显式披露：打包 prompt 的分节头写 `（N 条，已省略最早 K 条）`，拼接正文写 `> 已省略最早 K 条消息（超出 token 预算）`。
  不披露的话 LLM 会把"窗口内没提到"当成"没发生"，给出错误的确定性结论。

### 降级路径为什么不再调一次 LLM 汇总

需求是"区分不同群"，最终产物本来就是按会话分段的 —— 汇总就是拼接 + 会话名标题。省掉一次调用（20-40s + 费用），更重要的是避免 LLM 在汇总时把不同会话的内容串味或压掉细节。`concat_sections()` 是纯字符串函数，签名里没有 summarizer 参数（有测试守住这一点）。

跨会话洞察（"今天几个群都在聊同一件事"）如果以后要，应该做成**可选开关**再加一层，而不是默认行为。

### 实测依据

MiniMax-M2.7-highspeed，`max_tokens=4096`：

| 场景 | prompt 字符 | 真实 token | 耗时 | 结果 |
|------|------------|-----------|------|------|
| 3 群 / 24h | 19,176 | ~8.1K | 8.8s | ✅ |
| 6 群 / 6h | 192,292 | ~81K | 26.1s | ✅ |
| 6 群 / 24h（清洗 XML） | 321,668 | ~135K | 25.3s / 重跑 40.6s | ✅ |
| 6 群 / 24h（未清洗） | 703,597 | **299,247** | 3.8s | ❌ 上下文超限 |

**延迟由输出长度决定，不是输入长度**：135K token 输入输出 480 字 → 25.3s；同样输入输出 1542 字 → 40.6s。固定开销约 18s，之后约 14ms/字，输出 ~2900 字就撞旧的 60s 超时（现已对摘要路径单独放宽到 `DIGEST_LLM_TIMEOUT_SEC=180`，见 `doc/modules/ai-backend.md`）。这也是 `PACKED_OUTPUT_CONTRACT` 硬性限制"每会话 ≤200 字"的原因：6 会话 = 1200 字 ≈ 35s，余量充足。

**真实数据端到端**（走完整 `_generate_digest`，真实消息库 + 真实 LLM，outbox/推送/任务中心用替身）：

| 场景 | mode | LLM 调用 | prompt | 消息数 | 耗时 | 输出分段 |
|------|------|---------|--------|-------|------|---------|
| 线上 3 群 / 24h | `packed` | **1** | 36,768 字符 | 197 | 15.2s | 2 段（第三个群 24h 内无消息，正确跳过） |
| 最忙 6 群 / 24h | `packed` | **1** | 85,809 字符 | 2122 | 23.6s | **6 段全部到位** |
| 最忙 6 群 + `DIGEST_TOKEN_BUDGET=1` | `per_chat` | 6 | 各 4.5-8.3K 字符 | 1173（裁剪掉 949） | 21.2s | 6 组齐全，正文含"已省略最早 280 条消息"披露 |

三点值得记下来：① 默认预算下 6 个最忙群打包只用 1 次调用、离上限还很远（85,809 字符 ≈ 36K 真实 token，上限约 250K），**正常运营基本不会触发降级**；② 降级路径并发 3，6 次调用总耗时 21.2s 而不是 6×25s；③ 输出契约生效，6 群打包时模型给每群约 183 字，没有互相串味也没有漏群。

提示词分层修好后用同样口径复测（攒单群 1041 条 / 杨爽的金主们 999 条 / 冲凉中介群 432 条，近 25h）：

| 路径 | 调用 | 耗时 | 正文 h2 | 记忆 h3 |
|------|------|------|---------|---------|
| packed（1410 条） | 1 | 32.4s | **3 个**，正好等于会话数、顺序一致 | `分组共性 / 攒单群 / 杨爽的金主们 / 冲凉中介群`，836 字（额度 2600） |
| per_chat（kill switch，dropped 1306） | 3 | 12.7s | **3 个**（改造前同形态是会话数的 4 倍） | — |

降级路径的正文改用 `-` 列表分点（模型不再自己出 `##`），`## 会话名` 全部由 `concat_sections` 加。另外注意到一个不对称：打包受"每会话 ≤200 字"约束（3 会话 613 字），降级不受约束（3 会话 1277 字），**降级反而更详细** —— 因为每次调用只盯一个会话。这不是 bug，但调优输出长度时要记得两条路径的口径不同。

## 记忆更新

每次摘要后（`memory_enabled` 为真时）调用 `generate_memory_update_prompt()`，让 AI 在旧记忆基础上写一份新记忆，下次摘要作为"近期记忆"注入 prompt，形成跨次记忆累积。

**一个分组一份记忆**（组级单一记忆）：打包摘要天然只产出一份结果，对应一份记忆。旧数据迁移时每个旧会话成为一个单会话分组，记忆 1:1 带过去。

### 按会话分块

一份记忆服务组内所有会话，下一轮又作为共用上下文喂回**每一个**会话，所以必须能看出哪句话是哪个会话的：

- 每块以 `### 会话名` 开头，禁止把 A 会话的事写进 B 会话
- 本次没有新内容的会话保留上一版要点并标「（无新增）」，不整块删掉
- 无法归属到具体会话的旧内容（早期按单群口吻写的）归入开头的 `### 分组共性`

**为什么必须这样**：改造前提示词是"用第一人称写…群聊氛围和活跃度"，生产上 dg_001「聚沙成塔」配了 5 个会话，记忆却通篇是"我发现**我们这个群**呈现出脉冲式活跃模式"、零会话归属；而这份记忆会作为共用上下文喂给「妈妈和宝贝们」「长期回收各类购物卡券2群」等另外 4 个会话。加上 `### 分组共性` 这一去处后，旧的单群口吻记忆会在下一轮被自然重排，不需要数据迁移。

### 字数上限随会话数放大

```python
memory_char_budget(n) = min(2000 + 300 * (n - 1), 4000)
```

`group_memory_budget(group)` 按**真正会被摘要**的会话数（`enabled` 且 `chat_id` 非空）算，scheduler 拼 prompt 时用同一个函数 —— 两边口径不一致的话，prompt 里承诺的字数和落盘时的截断位置就是两个数。

封顶 4000 是因为 `memory_tokens` 直接从打包预算的 `available` 里扣，记忆越长越早触发降级。

**2000 是按单群定的、而且已经不够**：dg_003 只有 1 个会话，记忆就正好顶在 2000 字符，尾部停在 `\n\n#### 4.` —— 一个只有标题没有内容的残块。

### 截断按块边界回退

`truncate_memory(text, budget)` 不再盲切 `[:budget]`：优先退到最后一个 `### ` 块的开头（整块丢掉写不完的那段），其次退到最后一个空行；边界太靠前（不足 budget 一半）说明这份记忆没有分块结构，才硬切。

### 写入路径：必须走 `update_digest_group_memory(group_id, memory)`

配置是**整份 JSON 全量覆盖写**，而写它的线程有三类：WebUI HTTP 线程池（`max_workers=20`）、scheduler 线程池（`max_workers=3`）、agent 工具所在的 router 消息线程。`save_assistant_config` 本身是 tmp + `os.replace` 原子写，但 **read-modify-write 不是原子的**，且各线程持有的是**不同的内存副本**。

原来的写法 `dg.memory = new[:2000]; save_assistant_config(self._config)` 有两条实测的丢数据路径：

| 方向 | 场景 | 后果 |
|------|------|------|
| 丢记忆 | WebUI 保存配置 → `update_config` 把 `self._config` 换成新对象 B；而正在跑的 `_generate_digest(dg)` 里的 `dg` 还属于旧对象 A（提交线程池时就传进去了） | 记忆写在 A 上、落盘的是 B → **这次 LLM 生成的记忆静默消失** |
| 丢用户改动 | 摘要线程在 WebUI 保存**之前**读的 config、**之后**写盘 | 直接覆盖掉用户刚改的配置 |

修复分三层，缺一不可：

1. **`mutate_config(mutator)`** —— 唯一合法的"改配置"入口：锁内从磁盘**重读** → 应用 mutator → 落盘 → 返回落盘那份对象。写入基底永远是磁盘最新值，从根上消除丢更新（模块级 `_CONFIG_LOCK = threading.RLock()` 只防文件撕裂，防不了丢更新）。
2. **`update_digest_group_memory(group_id, memory)`** —— 后台写记忆的窄接口，**按 id 定位**而不是按调用方手里的对象引用；字数上限也收拢到这里（`truncate_memory(memory, group_memory_budget(g))`，在 mutator 内算，因为额度取决于锁内重读到的会话列表），并让 `memory_rev += 1`。
3. **`_run_digest_in_pool(group_id, ...)` + `_load_group_fresh()`** —— 提交线程池时传 id 而不是对象，运行时从磁盘重读。"捕获旧对象"这个根因被**物理消除**，不是靠锁遮住的。分组在排队期间被删除时直接 `fail_task('分组已被删除')`。

WebUI 批量 `PUT /api/assistant/config` 用 `merge_digest_groups()` 合并，其中**已存在分组的 `memory` / `memory_rev` 一律以磁盘为准** —— 浏览器手里的 config 可能是几分钟前 GET 的，不豁免就会把后台刚写的记忆回滚成旧值。

### 手工编辑记忆

走独立端点 `PUT /api/assistant/digest-group-memory`，body `{id, memory, memory_rev}`，由 `cas_digest_group_memory()` 做乐观并发：

- `memory_rev` 匹配 → 写入，返回新的 `memory_rev`
- 不匹配（期间后台摘要写过）→ 返回 `{ok: false, error: "记忆已被后台摘要更新，已为你载入最新版本", memory, memory_rev}`，前端把磁盘最新值回填 textarea
- `memory_rev == -1` → 分组不存在

不能走批量 config PUT，因为那条路刻意让 memory 以磁盘为准。

### 解析失败的保护

`load_assistant_config` 解析失败时，**先把坏文件 rename 成 `.corrupt-<时间戳>` 再写默认配置**。原来直接用默认配置覆盖磁盘 —— 一次 `JSONDecodeError` 就永久抹掉整份配置，包括上面那 4408 字符不可再生的记忆。

群记忆可在前端群档案中查看和编辑（关闭 `memory_enabled` 则摘要不再更新记忆，清空即重置）。

## 双通道输出

| 通道 | 路径 | 用途 |
|------|------|------|
| Outbox | `outbox.add(notif_type="group_digest")` | 持久化到 SQLite，供前端/外部 Agent 拉取 |
| iLink | `ilink_push.send_message()` | 即时推送到微信私聊（仅当 `push_target=="ilink"`） |

两条路径独立：iLink 推送失败不影响 Outbox 持久化；推送结果通过 WebSocket 广播到前端，UI 实时显示成功/失败提示。

## 关键设计决策

### 1. cron 多行格式

`cron_expr` 采用多行格式，每行一个触发时间（如 `0 9 * * 1-5\n30 18 * * 1-5`）。相比单行逗号表达式，多行更易读、不易因反复编辑而出错，前后端校验规则一致。配合前端时间芯片选择器，简单用户用 chip 生成 cron，高级用户可直接编辑。

### 2. schedule 与 cron_expr 共存

两字段都持久化，`cron_expr` 优先级更高。前端通过 `buildCronExpr()` / `parseCronExpr()` 双向同步。

### 3. custom_prompt 替代而非追加

群档案（style）是输出风格上下文，不应被自定义指令覆盖；custom_prompt 是用户对该群摘要的"额外要求"，作为 system 输入完全替代默认指令，让用户获得完全控制权。

> **例外**：多会话打包时的 `PACKED_OUTPUT_CONTRACT` 仍然追加，见决策 6。

### 4. 风格预设作为 system 追加

style 预设是轻量调整（行动项优先 / 完整复盘 / 极简速览），追加到默认 system 之后，不替代默认角色定义。与 custom_prompt 互斥（选了自定义就不叠加预设）。

### 5. 无消息也入队，但取消息失败不算无消息

回溯窗口内无新消息时仍写一条 Outbox（标注"无新消息，摘要跳过"），让用户确认调度确实执行过，便于排查"为什么没收到摘要"。

但**全部会话取消息失败**（DB 锁、连接断开等）时 `total_raw` 同样是 0，必须与"窗口内真的没消息"区分开：前者 `_tc_fail` 并把每个会话的错误写进 error，**不写 Outbox**。否则一次故障会变成一条"该时间窗口内无新消息"，用户会以为群里真的没人说话。部分会话失败则继续跑，失败的会话由 `failed_chats` 与正文里的 `（本次摘要失败：…）` 暴露。

### 6. 打包输出契约是结构约束，不是风格指令

`PACKED_OUTPUT_CONTRACT`（按会话分段 / 禁止跨会话合并 / 禁止漏群 / 每会话 ≤200 字）**即使用户设了 custom_prompt 也必须追加**。custom_prompt 替代的是"怎么摘要"，契约约束的是"输出必须分成几段"——少了它，6 个会话会被混写成一篇，`## 会话名` 的分段结构就没了。

第 4 条"每会话 ≤200 字"是硬性的延迟护栏：实测延迟由输出长度决定（固定 ~18s + ~14ms/字），6 会话 × 200 字 ≈ 35s，在 180s 超时内余量充足；不限长时输出 2900 字就会撞旧的 60s。

### 7. 一个分组一个 task_center 任务

一次触发 = 一条 Outbox 记录 = 一个 task = 一个可重推单元。降级成逐会话只是内部实现细节，用户视角始终是"这个分组的一次摘要"。

拆成 N 个 task 会破坏两处既有机制：重推正文的三级兜底（`outbox_id` → 时间窗匹配 → `task.result`）依赖"一个 task 一份完整正文"；推送三态（`delivery.aggregate_status`）是按一次 `send_text` 聚合的，天然对应"一组一次推送"。组内进度用 `update_task(progress=...)` 表达（`正在获取消息 (2/6)` → `超出预算，改为逐会话摘要 (3/6)` → `推送中`）。

### 8. single 模式也要点名会话（放弃了"与改造前逐字节一致"）

分组改造当天曾刻意保证：`single` 模式（组内只有一个有内容的会话）不追加任何契约、prompt 与改造前逐字节一致，好让迁移后的单会话分组零行为变化。**这条保证现在故意放弃了。**

`plan_digest` 判定 single 用的是**有消息的会话数**，不是分组大小。生产上 dg_001「聚沙成塔」配了 5 个会话，最新一条 Outbox 却是 `digest_mode=single, chats=["509助力"]` —— 只有那一个会话有新消息。旧行为下这次摘要正文里没有任何会话名，接着被写进**全组共用**的记忆，下一轮又当上下文喂给另外 4 个会话。

所以 single 现在也追加 `SINGLE_HEADING_CONTRACT`（第一行 `## 会话名`），user prompt 也带上 `## 本次摘要的会话`。代价是单会话分组的推送正文多一行标题 —— 换来的是记忆可归属。

## 代码位置

| 组件 | 文件 |
|------|------|
| DigestScheduler（编排：`_collect_chat_units` / `_render_digest` / `_render_once` / `_render_per_chat` / `_digest_system_prompt` / `_update_group_memory` / `_publish_outbox` / `_push_and_finish`） | `src/assistant/scheduler.py` |
| DigestGroup / DigestChat / GroupProfile / 迁移与校验 / `mutate_config` / 记忆窄接口 / 记忆额度与截断（`memory_char_budget` / `group_memory_budget` / `truncate_memory`） | `src/assistant/config.py` |
| 过滤 + XML 清洗 + prompt 构建（`build_digest_prompt` / `build_packed_prompt` / `generate_memory_update_prompt`）+ 三个结构契约（`SINGLE_HEADING_CONTRACT` / `PACKED_OUTPUT_CONTRACT` / `PER_CHAT_NO_HEADING_HINT`）+ 预算降级（`plan_digest` / `trim_oldest` / `concat_sections` / `digest_token_budget`） | `src/assistant/digest.py` |
| LLM 异常（`LLMContextOverflowError` 触发降级） | `src/summarize/errors.py` |
| Outbox | `src/assistant/outbox.py` |
| 推送与三态聚合 | `src/im/delivery.py` |
| iLink 推送 | `src/wechat/ilink_push.py` |
| 前端 DigestGroupCard / Editor / **MultiChatPicker**（多选会话） | `ui/src/components/AssistantPanel.jsx` |