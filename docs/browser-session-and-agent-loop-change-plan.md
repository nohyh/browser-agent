# Browser Session 与 Agent Loop 改造说明

> 文档状态：待实施  
> 编写时间：2026-07-30  
> 文档目的：区分两次测试分别暴露了什么问题，说明准备修改的模块、修改逻辑、改造前后差异和验收标准。

## 1. 结论与需求来源

本轮准备实施的改动来自两组不同的测试，它们解决的是两个层次的问题：

1. **“控制当前浏览器”测试**暴露的是 Browser Session 生命周期问题。核心是 existing 模式没有做到“只连接、不启动”，查询和探测还可能意外冷启动新的浏览器 profile。
2. **最新 `test-a` Agent 任务日志**暴露的是 Agent Loop 的证据、日志和上下文问题。核心是日志不能忠实反映 LLM 实际输入，以及页面跳转后的瞬时空状态可能导致 Agent 过早完成。

两组问题会在“浏览器状态是否可信”这个位置交汇，但不会混在一起修。Session 层负责保证连接对象正确且生命周期可控；Agent 层负责保证观察稳定、证据充分、日志可审计。

本次分类使用的直接依据是：

- 当前浏览器测试：`test-1` 的后端 session 状态、agent-browser runtime 状态，以及关闭 profile 后的 409 响应。
- 最新 Agent 测试：`backend/logs/conversations/test-a.md`。

| 需求编号 | 需求来源 | 主要问题 | 修改范围 |
| --- | --- | --- | --- |
| S1～S6 | 控制当前浏览器测试 | 双 profile、existing 模式连接不可靠、查询会冷启动、关闭后 409 恢复不清晰 | Session 生命周期与 API |
| A1～A5 | 最新 `test-a` 日志 | LLM 输入日志失真、页面跳转后过早完成、任务进度重复累积 | Agent Loop、Trace、上下文 |
| R1～R6 | 两次测试的回归确认 | 已完成能力不能在重构中退化 | 自动化测试与真实浏览器测试 |

## 2. 来自“控制当前浏览器”测试的改动需求

### 2.1 测试中观察到的现象

创建 `mode=existing` 的 session 后，出现了两个新的浏览器 profile。关闭这些窗口后调用 `/agent/run`，后端返回：

```json
{
  "detail": "Browser session 'test-1' is not ready or is no longer active; start it via POST /browser/session/start first"
}
```

进一步检查发现：

- 后端保存的 session 是 `mode=existing、ready=false、url=about:blank`。
- agent-browser daemon 仍然存活，但 `browserLaunched=false、pageCount=0`。
- `test-1` 对应的 agent-browser 外部 session 不是新 session，而是历史残留 session。
- 看似只读的 `get url` 在浏览器已经关闭时会冷启动新浏览器，并返回 `about:blank`。

因此，409 本身是正确的保护行为。真正要修改的是 409 之前的启动、探测和恢复逻辑。

### 2.2 S1：统一 Session 生命周期入口

**准备修改的位置**

- Browser Session 启动、连接、健康检查和关闭服务。
- `/browser/session/start` 和 `/browser/sessions/{id}` 对应的状态逻辑。

**改造前**

一次 session 启动混合了两条路径：

1. 临时 CLI 使用 `get url` 创建或探测 session。
2. 长驻 MCP 再次调用 `get_url` 检查同名 session。

CLI 和 MCP 都可能触碰生命周期，后端无法明确知道是谁创建了浏览器、是否复用了历史 daemon，也无法保证 existing 模式没有新建 profile。

**改造后**

生命周期只允许通过长驻 MCP 进入：

- `mode=existing`：只执行显式 `connect(target=cdp_url)`。
- `mode=isolated`：只执行显式 `open()`。
- 健康检查：只执行无启动副作用的 `session_info`。
- existing 关闭：只释放后端连接和自己拥有的 runtime session，不关闭用户的外部浏览器。
- isolated 关闭：关闭后端创建的浏览器和 runtime session。

CLI 不再参与 session 的创建、探测和关闭。一个 session 只有一个生命周期所有者。

### 2.3 S2：existing 模式严格执行“只连接、不启动”

**改造前**

existing 模式虽然接收 `cdp_url`，但后续仍通过 `get url` 探测。目标浏览器不可连接、session 状态丢失或外部 daemon 状态不一致时，agent-browser 可能冷启动一个新的空白浏览器。

这会造成两种严重后果：

- 用户以为 Agent 在控制当前登录浏览器，实际控制的是新的空白 profile。
- 登录态、Cookie、扩展和已打开页面全部不符合预期。

**改造后**

existing 模式遵守以下硬规则：

1. 只能连接用户明确提供的 CDP 目标。
2. CDP 不可访问时直接启动失败。
3. 连接失败时不得调用 `open`，不得创建本地 profile，也不得自动回退。
4. 连接成功后必须通过 `session_info` 确认浏览器已连接，并确认至少存在一个可控制页面。
5. 返回给调用方的 URL、页面数量和连接模式必须来自实际连接状态。

因此，existing 模式成功代表“已经连接到指定浏览器”，而不是“某个浏览器现在可用”。

### 2.4 S3：健康检查必须没有副作用

**改造前**

`get url` 和 snapshot 一类工具可能在 session 不活跃时自动启动浏览器。使用这些工具判断 session 是否存活，会在读取状态的同时改变状态。

**改造后**

健康检查只读取 daemon 与浏览器运行态，不导航、不创建页面、不启动浏览器：

```text
检查前：浏览器关闭
执行健康检查
检查后：浏览器仍然关闭
返回：disconnected
```

验收时会比较检查前后的浏览器进程、页面数量和 profile 数量，确保查询不会造成变化。

### 2.5 S4：隔离公开 Session ID 与底层运行 Session

**改造前**

用户传入的 `browser_session_id` 直接作为 agent-browser 的底层 session 名称。类似 `test-1` 的名称可能与历史 daemon、其他后端进程或之前的测试冲突。

后端重启后内存 registry 清空，但 agent-browser 的 session 文件仍可能存在，造成后端误接管历史状态。

**改造后**

对外仍使用用户提供的 `browser_session_id`，后端内部为每次实际启动生成唯一的 runtime session ID：

```text
公开 ID：test-1
运行 ID：browser-agent-<run-id>-<随机标识>
```

后端保存公开 ID 到运行 ID 的映射。这样可以做到：

- 同名公开 ID 的幂等请求仍能返回同一个后端 session。
- 重启或重新连接时可以创建新的运行 session，不会误用历史 daemon。
- 后端只清理自己创建并记录的运行 session，不误伤其他进程的 session。

如果后端未来需要跨进程恢复 session，再单独增加持久化映射；本轮不引入复杂的跨重启恢复。

### 2.6 S5：明确关闭、断开和重连语义

**改造前**

session 主要用一个 `ready` 布尔值表达状态。浏览器关闭后，调用方只能知道 `ready=false`，不能区分正在启动、连接断开、配置错误或已主动关闭。

再次调用 start 的行为也不够明确，可能重新使用失效 daemon。

**改造后**

API 对外提供最小且明确的状态：

| 状态 | 含义 | 是否允许 `/agent/run` |
| --- | --- | --- |
| `starting` | 正在创建或连接 | 否 |
| `ready` | 浏览器和页面均可控制 | 是 |
| `disconnected` | daemon 存在，但目标浏览器或页面已消失 | 否 |
| `error` | 启动或连接失败 | 否 |
| `closed` | 用户主动关闭或删除 | 否 |

为兼容现有调用方，`ready` 字段可以保留，但由 `status == ready` 推导，不再作为唯一状态来源。

恢复规则保持简单：

- existing session：重新执行对原 CDP 目标的 connect。
- isolated session：创建一个新的运行 session 和一个新的浏览器。
- `/agent/run` 不自动恢复，避免在提交评论、发布内容等写操作前静默切换 profile。

### 2.7 S6：让 409 返回可操作的结构化原因

**改造前**

409 只返回一段文本，调用方不知道浏览器是被关闭、仍在启动，还是连接目标失败。

**改造后**

409 保留，但返回结构化信息：

```json
{
  "code": "browser_session_disconnected",
  "browser_session_id": "test-1",
  "mode": "existing",
  "status": "disconnected",
  "browser_launched": false,
  "page_count": 0,
  "action": "restart_or_reconnect_session"
}
```

这不会把错误隐藏成成功，也不会在 `/agent/run` 内自动创建另一个 profile。

## 3. 来自最新 `test-a` Agent 日志的改动需求

### 3.1 测试结果概览

测试任务是打开 React 官网、切换黑夜模式、再切换到中文。结果最终成功：

- 总耗时约 38 秒。
- LLM 调用 5 次。
- 总 Token 为 27,934。
- open 约 8.7 秒。
- LLM 调用累计约 26.6 秒，是主要耗时来源。
- 单次 snapshot 为 25～5,196 字符，已不再存在 4 万字符级 snapshot。

操作路径本身合理：

1. 打开 React 官网。
2. 点击黑夜模式。
3. 根据按钮变成 “Use Light Mode” 确认黑夜模式生效。
4. 进入 Translations。
5. 点击 Simplified Chinese。
6. 返回最终答案。

问题主要出现在最后一步的证据稳定性和日志可观测性。

### 3.2 A1：让“LLM 输入日志”忠实反映实际输入

**改造前**

真实 LLM 输入会收到结构化 task context，但 trace 写日志时只保留每项的 `name/status`。`agent_progress` 因为没有这两个字段，最终在日志中显示为 `{}`。

同时，snapshot 的日志摘要只保留 hash 和字符数，丢失了 LLM 实际可见的 `url/origin/title`。这导致日志中看不到最终 URL 证据，却看到模型直接宣布已经进入中文站。

因此，当前标题为“LLM 输入”的日志实际上只是过度裁剪的摘要，容易让排查者错误判断模型只看到了 `succeeded`。

**改造后**

日志仍然不保存完整大页面，但必须保存与模型决策有关的结构化摘要：

- observation：`url/origin/title`、snapshot hash、snapshot 字符数、是否截断。
- agent progress：上一步评价、当前记忆、下一目标的限长摘要。
- tool result：工具名、状态、关键 data 摘要、error、effect。
- 输入统计：实际输入字符数、观察字符数、Token 使用和截断信息。

日志标题改成“LLM 输入摘要”，明确它不是完整 prompt；或者提供一个字段说明哪些数据被省略。

改造后的日志应能够回答：

1. 模型是否看到了目标 URL。
2. 模型是否看到了工具的真实数据，而不只是 succeeded。
3. 哪些内容被截断。
4. 模型根据什么证据宣布完成。

### 3.3 A2：补齐工具参数与 Action Effect 日志

**改造前**

工具调用日志有参数，但对应工具结果中的 `arguments` 经常变成 `{}`，不便于把结果与具体动作对应起来。

工具执行完成时立即记录 result；页面变化要等下一次 snapshot 才能计算。因此日志里的 effect 一直是：

```json
{
  "dispatched": true,
  "page_changed": null
}
```

虽然内存中的 task context 后续可能已经更新为 `url_changed=true` 或 `snapshot_changed=true`，日志却没有同步记录。

**改造后**

每个动作在日志中形成一条完整链路：

```text
action dispatched
→ 原始工具结果
→ 动作后观察
→ effect finalized
```

最终 effect 至少包含：

- `dispatched`
- `page_changed`
- `url_changed`
- `snapshot_changed`
- `stabilized`

工具结果保留原始调用参数的安全摘要。敏感字段继续脱敏，不通过完整 URL 查询参数泄漏 token。

### 3.4 A3：页面跳转后的空 Snapshot 不能直接完成

**改造前**

点击中文链接后约 78 毫秒就进行了 snapshot，返回：

```text
(no interactive elements)
```

LLM 随后宣布任务完成。即使 snapshot 原始数据中包含中文站 origin，也只能证明 URL 已经改变，不能证明中文页面加载完成，更不能证明黑夜模式在新站点仍然有效。

**改造后**

对可能改变页面的动作执行轻量稳定判断：

1. 执行动作。
2. 获取一次动作后状态。
3. 如果 URL 改变且 snapshot 为空，判定为“跳转中”，不进入完成判断。
4. 在受控的短等待后重试一次观察。
5. 获得非空页面、明确加载失败或达到稳定超时后，再交给 LLM 决策。

这一逻辑只在“URL 已变化且页面观察为空”时触发，不会给每次普通点击增加固定等待。

### 3.5 A4：完成判断必须基于稳定的最终状态

**改造前**

模型可以依据先前步骤的 memory，加上最新 URL 变化直接完成。对于会跨域或重新加载的网站，先前验证过的视觉模式、登录态或表单状态可能不会保留。

**改造后**

完成前区分两类证据：

- **动作已经发生**：例如确实点击过黑夜模式按钮。
- **最终状态仍然满足要求**：例如进入中文站后仍能看到黑夜模式处于启用状态。

如果用户要求的是最终页面状态，必须由稳定后的最新观察确认；如果用户只要求执行一次动作，可以使用已记录的动作效果作为证据。

对于本次 React 任务，理想完成证据是：

```text
当前 origin 属于简体中文站
+
当前页面已经加载
+
当前主题控件表明黑夜模式仍处于启用状态
```

### 3.6 A5：任务进度只保留最新版本

**改造前**

每轮 LLM 都会追加一份新的 `agent_progress`。本次任务的 task context 条目从 2、4、6 增加到 8，多份 memory 重复描述相同任务和已完成步骤。

虽然已有总长度限制，但在触发限制前仍会重复消耗输入 Token。

**改造后**

task context 分成两类：

- `latest_progress`：始终只有一份，新决策替换旧决策。
- `recent_tool_results`：按顺序保留最近的结构化工具结果。

这样不改变 Agent 的记忆能力，但避免把多份近似 memory 重复传给模型。

本轮不实施复杂的动态工具暴露。最新测试中 snapshot 已经较小，主要耗时来自 5 次 LLM 调用和每轮重复的稳定提示词、任务进度与工具说明。先减少重复进度并利用稳定前缀缓存，比引入新的工具发现协议更简单。

## 4. 已经生效、本轮只做回归保护的能力

以下项目在最新日志中已经正常工作，不作为新的功能重新实现：

### R1：元素 ref 自动规范化

LLM 曾输出 `e8`，执行前已自动转换为 `@e8`，点击成功。后续需要保留对以下格式的兼容：

- `e107`
- `@e107`
- `[ref='e107']`
- `[ref="e107"]`

### R2：工具结果统一 Envelope

最新日志中的工具结果已经统一为：

```json
{
  "status": "succeeded",
  "data": {},
  "error": null,
  "effect": {}
}
```

本轮只补充日志缺失字段和最终 effect，不重新设计结果协议。

### R3：Snapshot 长度控制

本次最大 snapshot 为 5,196 字符，已经比历史 42,965 字符明显下降。保留现有限长和 refs 去重逻辑。

语义块 snapshot、Reddit 首页帖子结构化、详情正文与评论结构化仍是后续第二阶段能力，不与本轮 Session 修复混合实施。

### R4：完成后立即退出

最终决策为 `completed`、`actions=[]`，Agent 随即退出，没有继续调用 title、eval 或 read。

### R5：北京时间

日志时间戳已经使用 `+08:00`，本轮只做回归测试。

### R6：Token 累计

日志已经记录每次 LLM 用量和最终累计值。本次 5 次调用合计：

```json
{
  "input_tokens": 25870,
  "output_tokens": 2064,
  "total_tokens": 27934,
  "reasoning_tokens": 1200
}
```

本轮补充阶段耗时和截断观测，不改变现有 Token 统计口径。

## 5. 改造前后整体流程对比

### 5.1 控制当前浏览器

**改造前**

```text
POST session/start(existing)
→ CLI get url
→ 可能创建或复用历史 daemon
→ 可能冷启动新 profile
→ MCP 再次 get_url
→ 只要返回 URL 就标记 ready
```

**改造后**

```text
POST session/start(existing)
→ 创建唯一 runtime session
→ MCP connect(明确的 CDP 目标)
→ MCP session_info 无副作用核验
→ 浏览器已连接且存在页面
→ 标记 ready

任何连接失败
→ 返回结构化错误
→ 不启动新 profile
```

### 5.2 Agent 动作与完成判断

**改造前**

```text
点击链接
→ 工具返回 succeeded
→ 立即 snapshot
→ snapshot 可能处于跳转中的空状态
→ LLM 根据 URL 或历史 memory 宣布完成
→ 日志看不到 URL、完整工具结果和最终 effect
```

**改造后**

```text
点击链接
→ 保存结构化工具结果
→ 获取动作后页面状态
→ 计算 URL、snapshot 是否变化
→ 若 URL 已变化但页面为空，进行一次受控稳定重试
→ 写入最终 action effect
→ LLM 根据稳定状态和最新进度决策
→ 完成证据和日志中的状态能够对应
```

## 6. 准备修改的模块

| 模块 | 准备修改的职责 | 不在本轮做的内容 |
| --- | --- | --- |
| Browser Session 服务 | start/connect/open/health/close 单入口；公开 ID 与运行 ID 隔离 | 跨后端重启恢复全部 session |
| Session API | 返回明确状态和结构化 409；支持显式 reconnect/restart | `/agent/run` 自动重启浏览器 |
| Agent Loop | 动作后稳定观察；完成前使用稳定最终状态 | 针对 React、Reddit、X 编写站点特例 |
| Task Context | 最新 progress 替换旧 progress；保留最近工具结果 | 向量记忆或外部长期记忆 |
| Trace | 记录 LLM 可见的关键摘要、参数、effect、截断与耗时 | 重新记录完整页面和完整 prompt |
| Metrics | 保留精确 Token；增加阶段耗时和截断计数 | 自建 Token 估算替代供应商用量 |

## 7. 实施顺序

### 第一阶段：Session 生命周期

1. 先用测试固定当前 existing、isolated、关闭和 409 行为。
2. 添加失败测试，证明 health/query 不得启动浏览器。
3. 添加失败测试，证明 existing 连接失败不得创建 profile。
4. 统一为 MCP 生命周期入口。
5. 增加 runtime session ID 映射和结构化状态。
6. 完成 Session 相关全量回归。

这一阶段完成后，先解决“双 profile”和“控制错浏览器”的高风险问题。

### 第二阶段：Trace 与 Action Effect

1. 添加测试，证明日志能看到 origin/title 和工具 data 摘要。
2. 添加测试，证明 `agent_progress` 不再显示为无意义的 `{}`。
3. 添加测试，证明工具结果保留对应参数。
4. 在动作后观察完成时记录 finalized effect。
5. 检查日志脱敏和体积。

这一阶段完成后，日志才能作为判断 LLM 行为的可信依据。

### 第三阶段：页面稳定与上下文精简

1. 添加“URL 变化后第一次 snapshot 为空”的失败测试。
2. 实现一次受控稳定重试。
3. 添加“最终状态未确认不得 completed”的测试。
4. 把多份 agent progress 收敛成一份 latest progress。
5. 比较改造前后的 LLM 调用次数、输入字符数、Token 和总耗时。

## 8. 验收标准

### 8.1 Session 验收

- existing 模式连接成功时，不产生新的浏览器 profile。
- existing 模式 CDP 地址无效时，启动失败且浏览器进程、页面数量不增加。
- 重复调用 session info、get session 等查询接口，不启动浏览器、不创建页面。
- isolated 模式一次 start 只产生一个运行 session 和一个浏览器。
- 用户手动关闭浏览器后，状态变为 `disconnected`。
- disconnected session 调用 `/agent/run` 返回结构化 409，且不会自动重启。
- 显式 reconnect/restart 后，只恢复一个正确目标。
- 历史同名 agent-browser session 不会被静默复用。

### 8.2 Agent 与日志验收

- `test-a` 同类任务只能在中文页面稳定加载后完成。
- 如果最终页面需要保持黑夜模式，必须在最终页面状态中得到确认。
- 日志能看到最终 origin/title、动作参数和 finalized effect。
- 日志中不再出现无法解释的 agent progress `{}`。
- 日志能够区分“工具已派发”“页面已变化”和“最终状态已稳定”。
- task context 中最多存在一份 agent progress。
- Snapshot、refs、历史工具结果不会重新无限累积。

### 8.3 回归与真实测试

- 运行现有完整单元测试套件。
- 增加 Session 生命周期、空 snapshot 稳定和 trace 忠实度测试。
- 使用真实 LLM 执行一次 React 任务，检查最终状态、日志和 Token。
- 使用真实浏览器 CDP 执行一次 existing 模式测试，确认没有新 profile。
- 手动关闭目标浏览器，再验证 disconnected、409 和显式 reconnect。

## 9. 本轮明确不做的事情

为避免把修复范围变得过大，本轮不同时实施以下能力：

- 动态暴露全部工具或新的多层工具发现协议。
- Reddit/X 专用帖子、评论和时间线解析器。
- 完整语义块 snapshot 系统。
- 复杂的跨进程 session 持久化和自动恢复。
- `/agent/run` 中的隐式浏览器重启。
- 依赖临时 JavaScript eval 的站点特例。

这些能力可以在 Session 生命周期和日志可信度稳定后，再根据新的真实日志决定优先级。

## 10. 最终预期

改造完成后，系统应满足以下行为：

1. 用户选择控制当前浏览器时，系统只连接指定浏览器，绝不创建替代 profile。
2. 查询 session 状态不会改变浏览器状态。
3. 浏览器被关闭时，系统明确报告 disconnected，不把任务交给 LLM，也不静默重启。
4. Agent 只根据稳定、可验证的最终页面状态宣布完成。
5. 日志虽然保持精简，但能够说明 LLM 实际看到了哪些关键证据。
6. task context 不再重复累积相同进度，Token 和耗时可以持续观测。
