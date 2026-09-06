# AgentDemo 项目原理与面试准备手册

适用方向：大模型应用与 Agent 开发。准备周期：2026 年 9 月 7 日至 9 月 11 日，9 月 12 日面试前复习。代码基线：`cf58c0751e9ad6d674a126ed86c566dc25e41264`，分析时 `test`、`run` 与对应远端分支一致。

这份手册要帮助你把 AgentDemo 讲成一个有实际执行过程、有工程边界、能解释取舍的项目。核心主线是：用户提出请求，系统恢复上下文，选择工具，受控执行，把证据交给模型生成回答，并保存过程；耗时工作还可以变成有状态的后台任务。

你应当优先掌握实际运行链路，再学习术语。面试时，对已经实现的部分讲代码和测试，对不足讲原因和改进方案。项目事实不等于个人贡献；下文的口述示例需要按你实际完成和理解的内容调整。

## 01 五天内应当达到的水平

### 学习顺序

第一次阅读先看第 02 至 04 章，理解项目定位、分层和一次请求如何结束。第二次重点读第 05 至 11 章，回答 Agent 岗位最常追问的工具规划、执行约束、MCP、RAG、记忆与异步任务。第三次用第 12 至 16 章练习答题、演示与自查。

不要把每个函数都背下来。你需要能画出请求链路，指出三个关键代码入口，解释两个失败场景，并承认一个可以改进的设计。正文中的 `[R01]` 等编号链接到基线代码，末尾有按主题整理的阅读索引。

### 六个必须先记住的事实

| 容易混淆的说法 | 当前代码的准确描述 |
| --- | --- |
| Agent 会自由理解任务并规划任意工具链 | 当前主要依靠规则路由和工具名匹配，已有有界循环，通用规划能力仍有限 |
| 已经使用原生 Function Calling | 模型请求没有传 `tools` 或 `tool_choice`，也没有处理 `delta.tool_calls` |
| 使用 LangGraph 就有断点恢复 | `graph.compile()` 未接入 checkpointer；会话和任务表持久化是另一套机制 |
| SSE 让回答逐 token 显示 | Gateway 接收上游流，Runtime 收集完整回答并清理后，通常用一次 `token` 事件输出 |
| 向量和关键词实现了混合召回 | 先尝试向量；没有有效结果才走关键词，再视意图回退到会话文档 |
| 后台任务可以保证所有工具只执行一次 | 有任务创建去重、抢占和租约机制；重试仍可能重复调用外部工具 |

这些边界并不会使项目失去价值。它们决定了你应该强调“可控执行与可观察的 Agent 应用工程”，并把通用规划、图恢复和效果优化作为后续方向。[R01] [R02] [R03] [R05] [R10] [R12]

## 02 如何介绍这个项目

### 三十秒介绍

> AgentDemo 是一个本地优先的个人 AI Agent。我用 FastAPI、React 和 LangGraph 串起知识检索、工具执行和回答生成，用 PostgreSQL 保存会话与任务。重点是工具执行可确认、可审计，并支持 RAG、会话摘要和后台任务重试。目前工具选择以规则为主。

只有当上述建设内容确实属于你的贡献时才使用“我用”。如果有借助 AI 编程或参考现有实现的部分，按第 13 章说明你的设计、验证和排错工作。

### 两分钟介绍

> 这个项目面向个人知识问答和带工具的任务处理。例如，用户上传一份技术资料后追问内容，或者给一个网页、GitHub 链接，要求先读取资料再总结。系统还支持把一个 prompt 放到后台执行，或设置一次性、间隔和每日任务。
>
> 后端的核心入口是聊天 SSE 接口。它先做用户认证和会话归属检查，再由 LangGraph 加载最近消息与摘要、检索知识、产生工具计划。工具不会由模型直接执行，而是统一交给 ToolExecutor，处理确认、参数校验、超时、结果规范化和审计。执行结果作为 observation 加入模型上下文，回答和相关元数据再落库。
>
> 我会重点展示三个部分。第一是本地插件和 MCP 工具共享执行边界，避免接入新工具后绕过治理。第二是知识检索与会话记忆分工，既能找资料，也能保持对话连续性。第三是后台任务的状态、重试、心跳和租约，让服务中断后的工作有恢复依据。
>
> 当前版本也有明确边界。工具规划主要是规则；RAG 是向量优先、关键词降级；后台任务是重新执行任务，不是恢复 LangGraph 节点；还需要进一步做真实模型评测和性能基准。我能结合代码说明这些选择如何影响可靠性和体验。

### 五分钟讲解顺序

| 时间 | 讲什么 | 必须落到的证据 |
| --- | --- | --- |
| 前 40 秒 | 用户场景与项目边界 | 上传资料问答、URL 读取、后台任务 |
| 接下来 60 秒 | 一次聊天的分层链路 | 路由、Runtime、Graph、Executor、数据库 |
| 接下来 90 秒 | 最熟悉的两个难点 | 确认前置和协议清理，或任务重试与租约 |
| 接下来 50 秒 | 如何验证 | 测试与离线场景评测的范围，真实链路还要测什么 |
| 最后 60 秒 | 取舍与下一步 | 规则规划、全量缓冲、无 checkpoint、检索评估 |

### 项目价值如何落到具体行为

聊天历史解决“刚才说的是什么”；RAG 解决“资料里写了什么”；工具解决“需要从外部获取或执行什么”；任务系统解决“工作超过一次前端请求怎么办”；审计与 Runtime 面板解决“出了问题，哪一步失败了”。这些职责共同构成应用价值。[R02] [R04] [R06] [R10] [R12]

“本地优先”指应用和主要数据可部署在本机。当前聊天与 embedding 仍可调用远端模型 API，检索片段和历史会进入请求上下文，所以不能把它说成“数据绝不出本机”或“全部离线推理”。[R05]

## 03 系统架构和各层职责

### 一张架构图

```text
用户
  ↓
React 工作区  对话  引用  工具确认  Runtime  后台任务
  ↓ HTTP JSON 与 SSE
FastAPI 路由  认证  会话归属  请求校验
  ↓
AgentRuntime 与 LangGraph  状态与执行顺序
  ├─ RagService → embedding 或关键词 → 知识片段
  ├─ ModelGateway → 远端兼容 API → 回答与摘要
  └─ UnifiedToolRegistry → ToolExecutor
                           ├─ 本地 Python 插件
                           └─ MCP Client → 外部 MCP Server

PostgreSQL 与 pgvector  保存会话  知识  审计  任务
TaskScheduler → AgentTaskRunner → 复用 AgentRuntime
```

### 为什么这样分层

| 模块 | 承担的工作 | 这样拆开的收益 |
| --- | --- | --- |
| API 路由 | 用户身份、资源归属、输入输出协议 | 业务执行不需要混杂 HTTP 细节 |
| AgentRuntime | 恢复上下文、规划、调用服务、生成与保存 | 前台聊天和后台任务可以复用 |
| LangGraph 与 Nodes | 把阶段、分支、循环连接起来 | 执行顺序可读，也便于针对节点和分支验证 |
| ModelGateway | 模型 HTTP 请求、消息拼装、embedding、摘要 | 便于替换模型接入方式和注入测试替身 |
| RagService | 分块、索引、搜索、引用 | 检索策略可以独立优化 |
| Registry 与 ToolExecutor | 工具发现与受控执行 | 接入来源改变时，执行约束仍集中管理 |
| Scheduler 与 Runner | 调度、抢占、进度、失败重试 | 耗时执行不依赖浏览器一直保持连接 |

这些是同一个后端中的模块边界。当前不是微服务架构，也没有通过消息队列拆出独立 worker 服务。个人项目采用模块化单体，部署和调试成本更低；规模扩大后再拆进程或服务。[R02] [R06] [R12] [R13] [R17]

### 启动时发生什么

FastAPI 的 lifespan 加载本地插件，再构造 MCP Client 和统一注册表，发现 MCP 工具，最后启动 TaskScheduler。退出时取消调度器任务。MCP 某个服务发现失败会记录错误并继续处理其他服务；启动时的远端发现仍可能增加等待时间。[R07] [R17]

本地插件由 `plugins/*/manifest.json` 描述，`entrypoint` 指向 Python 文件和函数。注册表导入模块后保存 handler。扩展很轻便，但插件代码在后端进程权限下运行，安装插件本身必须被视为授予代码执行能力，不能把 manifest 当成沙箱。[R06]

## 04 一次聊天请求的完整执行过程

### 正常路径

以“解释我上传资料中的租约机制”为例：

1. 前端发送 `POST /api/conversations/chat/stream`，携带消息和可选会话 ID。
2. FastAPI 检查 Bearer 会话令牌。指定会话时，检查它属于当前用户，否则返回 404。
3. Runtime 建立本次状态和 trace ID。新会话由图节点创建，已有会话加载使用。
4. 加载最近的 user 与 assistant 消息，以及当前会话的有效摘要；新会话可根据首条消息自动命名。
5. 保存本条用户消息。历史是在保存之前读取的，避免把本次输入作为历史重复注入。
6. 先检查是否属于需要早展示确认的普通 URL 请求；此例继续走检索。
7. RagService 检索资料，并按引用结构返回片段；被显式引用的 MCP resource 或 prompt 也可进入上下文。
8. Planner 判断是否调用工具。已有知识足够且没有工具意图时，计划为 `no_tool`。
9. Gateway 组织 system、历史和当前输入，加入摘要、引用、工具 observation 等辅助上下文，向模型请求回答。
10. Runtime 收集和清理模型输出，发出用户可见 `token`，保存 assistant 消息及元数据。
11. 尝试更新长对话摘要，然后发送 `done`。摘要模型失败通常跳过；`done` 的到达也可能被摘要耗时延后。

关键入口是 `chat_stream`、`AgentRuntime.stream` 和 `build_agent_graph`。[R01] [R02] [R04]

### 图状态是什么

`AgentGraphState` 是一个 TypedDict，保存当前请求的消息、历史、摘要、引用、计划、工具结果、observation、最终回答、trace ID 和工具轮数。可以把它理解成“各阶段共同填写的执行记录”。节点返回需要更新的字段，后续节点读取新状态。[R03]

当前图主要顺序执行，没有并行分支合并，也没有为工具列表配置复杂 reducer。`AgentGraphNodes` 在图状态与 Pydantic 的 `AgentExecutionState` 之间转换，以复用 Runtime 方法。代价是同一批字段在两个状态结构中维护，新增字段时要同步转换逻辑。[R01] [R03]

### 有工具时如何循环

```text
检索 → 计划
         ├─ 不需要工具 → 生成 → 保存回答 → 更新摘要 → done
         └─ 需要工具 → ToolExecutor → 记录 observation → 再计划
                          └─ 等待确认 → 更新摘要 → done
```

一般模式下，`execute_tool` 之后回到 `plan`，最多默认 3 个图内工具轮次。对于 read_file、Fetch、GitHub、Hugging Face 和 web_search，Planner 还会检查本轮是否已经执行过对应类别，避免重复执行。[R01] [R02]

注意，“默认 3 轮”不是“总共最多 3 次外部调用”。一次 web_search 的执行节点内部可以继续抓取最多 3 个结果页面，所以工具审计条数可能达到 4 条。严格控制成本需要把所有实际调用计入统一预算。[R02]

### 确认路径为什么不同

普通 URL 抓取如果需要确认，会在保存用户消息之后先产生计划。Executor 记录一条被确认门拦住的失败结果，前端把它显示为 blocked；随后图直接结束这一轮，不进入正常回答生成和 assistant 消息保存。这使用户能更早看到确认按钮。[R01] [R02] [R04]

点击 Confirm 后，前端请求 `POST /api/conversations/chat/confirm/stream`，携带原消息、工具名和参数。后端校验会话归属，建立一个新的 confirmed_tool 图，加载上下文、检索、执行指定工具、生成并保存回答；它不会再次保存原用户消息，也不会在执行后回到普通多轮计划。[R01] [R02] [R20]

这是应用层续跑协议。它没有调用 LangGraph 的 `interrupt` 和 `Command(resume=...)`，也没有 checkpoint。官方的节点级恢复需要 checkpointer；传一个 `thread_id` 本身不会保存图状态。[LangGraph 持久化说明](https://docs.langchain.com/oss/python/langgraph/persistence)

## 05 工具规划到底有多智能

### 当前规划逻辑

`_plan_next_step` 的主要顺序是：先避免重复调用，再判断 GitHub 引用、本地读文件、普通 URL、Hugging Face、联网搜索、工具清单问题，最后尝试通用 MCP 工具名匹配。早确认节点只提前处理部分普通 URL，主动排除 GitHub 和 Hugging Face 意图。[R02]

| 用户输入 | 对应路由 | 选择依据 |
| --- | --- | --- |
| 读取 README.md 第一行 | read_file | 读文件意图和路径提取 |
| 总结一个普通网页 URL | mcp.fetch.fetch | 抽取 URL，使用 Fetch MCP |
| 总结 GitHub 的 blob 链接 | GitHub get_file_contents | 提取 owner、repo、path、ref |
| 在 Hugging Face 查找模型或数据集 | Hugging Face MCP | 意图、资源类型和已注册工具匹配 |
| 今天有什么 AI 新闻 | web_search | 时效词、外部信息意图和搜索规则 |
| 用某种风格介绍项目 | 通常不调用工具 | 普通表达请求应避免误触发搜索 |

路由依赖工具已加载和配置可用。普通网页匹配和 Hugging Face 匹配有先后关系，不能据此保证所有 Hugging Face URL 都一定进入专用工具。应以回归场景和实际 trace 为准。[R02] [R25]

### Function Calling 和 MCP 的区别

Function Calling 解决“模型如何提出结构化工具调用”。通常需要把工具 schema 传给模型，并读取它返回的工具名与参数。MCP 解决“应用如何发现和调用外部服务的工具与资源”。两者位于不同层，可以一起使用。

本项目的 `ModelGateway.plan_tool_call` 是同步名称匹配：检查用户输入是否包含工具名称，匹配后返回空参数字典。它没有请求规划模型。`stream_reply` 的 HTTP payload 只包含 model、messages、stream 和 temperature，不含原生工具调用字段。[R05]

因此可这样回答：“我搭建了工具计划的数据结构和受控执行框架，目前把高频明确场景做成确定性路由；通用 Function Calling 是可以沿现有接口补齐的能力。”别把返回结构化 Python 对象称为已经实现了模型原生工具调用。

### 为什么先使用规则

对于“读这个 URL”“查今天的新闻”，规则更容易保证路由、参数和确认策略稳定，也容易写离线测试。代价是同义表达、复合任务、参数补全、根据工具结果再决策的能力有限。当前 Planner 基本依据原始消息和已经调用的类别，并没有一个理解 observation 的通用推理规划器。[R02] [R05]

如果要升级，可以保留明确 URL 的优先路由，其他请求交给支持结构化工具调用的模型；模型只提出候选计划，后端仍验证工具存在、参数合法、权限与确认满足，再交给 Executor。还要补工具调用 ID、重复计划检测、预算和多轮失败策略。以上是改进方案，并非当前实现。

### 联网搜索和页面抓取

搜索阶段会清理查询词，可调用模型将用户句子转换为简短搜索 query；失败时用规则回退。搜索 provider 有 disabled、mock 以及若干 HTTP 适配实现。默认配置是 disabled，存在 provider 类不代表当前服务或账户必然可用。[R02] [R24]

成功搜索后，Runtime 从结果中去重并选取最多 3 个 HTTP 或 HTTPS URL，通过 Fetch MCP 获取正文。当前代码给这一步传入 `confirmed=True`，视为搜索请求的一部分；仍经过 ToolExecutor 的 URL 校验、权限策略、超时和审计。直接 URL 抓取通常需要交互确认，两条路径的授权行为不同。[R02] [R08]

如果正文全抓取失败，但搜索成功，会明确退回标题、摘要和链接。搜索本身失败时有专门的失败回答，避免把模型猜测当作当天新闻。其他工具失败路径仍需分别检查，不能泛化成所有失败都完全阻止无依据回答。[R09]

## 06 ToolExecutor 的设计与真实边界

### 为什么必须统一执行

模型输出或规则计划只是“想执行什么”。是否真正执行必须由应用决定。若某个 MCP 或搜索增强路径直接调用 client，就可能绕过确认、超时或审计。项目把本地 handler 与 MCP tool 都适配为 `RegisteredTool`，统一交给 Executor。[R06] [R07] [R08]

实际顺序是：检查 enabled，检查是否缺少确认，然后验证参数；接着在超时约束下调度工具，限制输出，判断 MCP 错误结果，构造标准响应并写审计。确认检查发生在参数验证之前，说明被拦住的请求还没有实际运行 handler。[R08]

### 每一道检查解决什么

| 机制 | 当前做法 | 不能过度承诺的部分 |
| --- | --- | --- |
| 参数校验 | required、顶层类型、additionalProperties 和少数 format | 是 JSON Schema 子集，没有完整递归、枚举和范围约束 |
| 用户确认 | manifest 要求确认且未确认时阻止执行 | 确认接口仍缺与持久审批记录绑定的一次性凭据 |
| 超时 | 取 manifest 与全局超时较小值，使用 wait_for | 等待超时不等于同步线程已被强制终止 |
| 输出控制 | 截断字符串及字典顶层字符串，生成简短摘要 | 没有对嵌套列表和字典做统一总字节限制 |
| 错误统一 | exceptions、timeout、MCP isError 转为标准状态 | HTTP 请求成功仍可能是工具业务失败 |
| 审计 | 写用户、会话、任务、输入、摘要、耗时和 trace | 当前记录以结束后写入为主，不是完整的开始与结束事务日志 |

本地同步 handler 通过 `asyncio.to_thread` 运行，避免直接占用事件循环。`asyncio.wait_for` 管理等待和取消；已经运行的线程不能被当作可随意安全杀死的工作单元。若未来增加长时间或有写入副作用的插件，应考虑子进程、协作取消和进程级资源限制。[Python asyncio 文档](https://docs.python.org/3/library/asyncio-task.html)

### 为什么必须处理 MCP 的 isError

MCP 可能成功返回 JSON-RPC 消息，但消息内部 `isError=true`，表示工具执行失败。若只以 HTTP 200 或“没有抛异常”判断成功，数据库、前端和模型上下文会相互矛盾。当前会保留标准化内容和 isError，再转成 `failed`，同时记录错误原因。[R07] [R08]

面试可以举例：“Fetch 返回访问被拒绝，如果仍显示绿色成功，模型就可能把错误页当正文总结。我在执行边界把业务错误统一成失败结果，并把失败 observation 传下去。”是否可以说“我修复了”，取决于你的实际贡献。

### 审计信息怎么关联

聊天 Runtime 会生成本次执行 trace ID，ToolExecutor 又为每次工具调用生成自己的 trace ID。`tool_call` 和 `tool_result` 中的 trace 未必是同一个值。用户、会话、任务 ID 以及保存的工具结果帮助关联过程，但当前不是贯穿所有组件的统一分布式 trace。[R02] [R08]

进一步完善时，可以增加 run_id、tool_call_id 与 parent_trace_id，把开始、确认、执行和结束串起来，并在持久化前脱敏输入。当前审计会保存完整 arguments，摘要截断不等同于秘密信息脱敏。

## 07 MCP 接入如何理解

### 三个概念

MCP 的 tools 是可以调用的功能，resources 是可以读取的上下文材料，prompts 是可复用的提示模板。协议提供统一交互方式，但不会自动替应用完成授权、隔离和结果可信度判断。[MCP 规范](https://modelcontextprotocol.io/specification/2025-06-18/index)

本项目最完整的接入路径是 MCP Client：读取配置、发现工具、映射到统一注册表、由 Executor 调用。工具名称被加上 `mcp.<server>.<tool>` 前缀，以区分服务来源；provider_tool_id 保留服务端原始名称。[R07]

### 传输和发现流程

Client 实现了 stdio 子进程通信和 HTTP JSON-RPC 请求，包括 initialize、initialized 通知、tools/list、tools/call，也有 resource 与 prompt 的列举和读取。HTTP 路径可以解析 JSON 或 SSE 格式响应，并处理会话头。[R07]

要讲清实现深度：stdio 当前每次请求创建子进程并初始化，随后关闭；HTTP 请求也执行初始化序列。配置接受的 http、sse、streamable-http 名称不代表实现了所有历史 MCP 传输的长连接细节、重连、分页和全能力协商。持久连接池与完整协议兼容测试是后续工作。

### Server 模式的边界

`AgentDemoMcpServer` 提供了本地工具 schema 和调用 façade，也能列出本地资源与提示模板。当前 FastAPI 的 `/api/mcp` 路由主要是服务、资源和提示的查询；没有把这个 façade 接成完整对外 MCP JSON-RPC 传输入口。准确表述是“具备服务端适配层，客户端集成更完整”。[R07] [R29]

### 安全策略如何落实

MCP 工具映射会对 Fetch、GitHub、Hugging Face 和 HTTP 传输工具设置 network 权限，并通常要求确认。描述文本经过长度限制和部分指令词过滤。配置检查避免保存明文令牌，支持环境变量占位符；执行时会检查配置的访问策略。[R07] [R11]

Fetch URL 校验会拒绝非 HTTP 协议、localhost、指定元数据主机和字面私有 IP。它没有在这里解析域名后的 IP，也没有在这里逐跳验证重定向，不能声称已经彻底防住 DNS rebinding 或所有 SSRF。增加网络出口规则和重定向校验是合理改进方向。[R11]

MCP resource 和 prompt 的上下文读取走 Client 自身接口，并不经过 ToolExecutor 的 tools 审计路径。因此应把“工具调用统一受控”与“所有外部材料读取均已统一审计”分开描述。[R02] [R07]

## 08 RAG 从上传到引用

### RAG 解决什么问题

模型参数中的知识可能不包含用户文档，也无法保证记住最新私有资料。RAG 先从外部知识库找到相关片段，再把片段放到本次输入上下文，让模型依据资料回答。它不会修改模型参数，也不等同于微调。

本项目的知识对象是文档与 chunk：文档保存标题、用户归属、会话归属和来源；chunk 保存文本、序号和可空向量。回答引用会带 document_id、title、chunk_index、content、source_uri、score 与 retrieval_method。[R10] [R16]

### 入库链路

1. 路由检查会话归属，接收文本或文件。
2. 文本 PDF 使用 pypdf 提取页面文字，TXT 与 Markdown 解码为文本。
3. 删除空行并整理行内首尾空白，用默认 900 个字符分块，相邻块重叠 120 个字符。
4. Gateway 向 embedding API 批量请求向量，按返回 index 排序。
5. 文档和 chunks 保存到 PostgreSQL，向量列为 `Vector(1536)`。

这里按字符切分，不是按 token、句子或语义边界切分。`tiktoken` 出现在依赖中，不代表这条路径已经使用它。上传的是文本 PDF，未实现 OCR，也未在当前上传路径对文件体积做完整限制。[R05] [R10] [R16] [R19]

重叠的作用是避免一个概念正好在块边界被切断。例如上一块末尾说“租约的作用是”，下一块开头才说“给 worker 有时限的执行权”。重叠能保留局部上下文，但会增加存储、embedding 成本和重复命中。900 和 120 是当前默认值，不能说它们已经通过系统评测得到最优结论。

### 查询链路

首先把 query 转成向量，按 cosine distance 排序，转换分数为 `max(0, 1 - distance)`，过滤低于 0.45 的结果，默认最多 4 个。会话文档的优先级排在全局文档之前；“全局”指当前用户未绑定会话的文档，通常不是所有用户共享。[R10]

余弦相似度衡量两个向量方向是否接近，可以写作 `cos(q,d) = (q·d) / (||q|| ||d||)`。pgvector 的 `<=>` 表示余弦距离，`1 - distance` 可得到余弦相似度。当前仓库没有 HNSW 或 IVFFlat 的向量索引迁移，所以不要宣称已经做了 ANN 加速。[pgvector 文档](https://github.com/pgvector/pgvector)

如果向量无有效结果，关键词路径会从受作用域限制的最多 50 个候选片段里做匹配。英文用规范化词项，中文生成 3 字和 2 字片段并过滤噪声，词项上限 32。按命中词项数量和会话优先级排序。这不是 BM25，也没有中文专业分词模型。[R10]

如果仍没有结果，且问题明确在问“上传的文档”等会话材料，会回退到本会话的近期文档片段，retrieval_method 标记为 conversation_context，score 为 0。这个分数表示回退来源，不是相关性置信度。

### 降级的价值和代价

embedding 调用异常会返回空向量结果。入库仍可保存无向量的 chunk，查询可走关键词，因此没有 embedding 服务时仍具备有限的知识查找能力。当前文档状态仍可为 indexed，也没有自动补向量队列；需要区分“文本已入库”和“向量化完整成功”。[R10]

向量检索有结果时，不会再合并关键词结果，也没有 reranker、query rewrite、RRF 或去重优化。当前先 limit 再过滤阈值，且会话优先级高于相似度，可能导致候选不足或全局高相关资料没被选入。这是可以用对照评测验证的具体取舍。

### 如何评估和改进

先制作一组带标准来源的问答，标注每题应该命中的文档和片段。检索层看 Recall@k、MRR 和错误作用域比例；生成层看回答事实是否被来源支持、引用是否对应、无资料时是否说明不足；工程层看入库与查询延迟、失败率和成本。

例如 Recall@4 可以定义为“本题应命中的相关片段中，有多少进入前 4 个结果”。如果每题只标注一个目标片段，统计它是否进入前 4 个，就是该设置下的命中指标。定义清楚数据和分母，才能解释结果。

优化顺序可以是：先改善分块和文档结构保留，再做并行多路召回与重排，最后根据规模引入向量索引。每次只改变一个主要因素，用同一套题比较。当前项目的 12 条离线评测不是上述真实 RAG 效果评测。[R25]

## 09 记忆和上下文工程

### 三类信息的区别

| 类型 | 当前来源 | 解决的问题 |
| --- | --- | --- |
| 短期记忆 | 当前会话最近默认 12 条 user 或 assistant 消息 | 代词指代、刚提出的要求、连续问答 |
| 长期摘要 | 当前会话中 valid_to 为空的 MemorySummary | 保留偏好、项目事实、决定和未完成任务 |
| 知识检索 | 知识库中的文档片段 | 为具体问题提供可引用材料 |

12 条指消息条数，不是 12 轮对话。摘要也受会话 ID 限制，并非自动在所有新会话共享的全局个人画像。[R02]

### 摘要什么时候生成

回答保存后，Runtime 加载这个会话的全部 user 和 assistant 消息；数量超过近期窗口时，将全部消息和已有摘要交给模型生成新摘要。已有有效摘要就更新，否则新建。模型失败或返回空内容会跳过摘要刷新。[R02] [R05]

读取时最多注入 3 条有效摘要，但刷新时只更新最近 1 条有效摘要。这反映当前实现而非严格的一条活跃摘要不变量。若未来允许手工新增或迁移历史摘要，需要防止过时内容并存和互相矛盾。

### 需要明确的局限

当前不是“仅总结被挤出窗口的旧消息”，而是反复读取并摘要全部历史。若每轮都增长一条输入量，长期累计处理量可能呈二次增长趋势；实际开销还受摘要频率和消息长度影响。摘要更新在 done 前，可能延长结束等待。[R02]

虽然 Message 有 token_count 字段，依赖中有 tiktoken，但当前路径没有完整的 token 预算器。按消息条数截断无法约束一条超长消息、多个工具输出和知识片段占用的总上下文。最自然的改进是按 token 分配历史、摘要、证据与工具预算，并异步增量更新摘要。

### 如何防止记忆污染

当前 system prompt 提醒模型优先参考会话中的用户事实，在无证据时说明缺失，并保留来源。但是提示约束无法保证模型不会从错误材料中提炼出错误记忆。[R05]

后续可以把用户明确表达的偏好、模型推断和外部材料分开保存，记录来源消息 ID 与有效时间，并提供更正和删除入口。当前已具备摘要查询、禁用和删除接口，可作为用户纠错的基础；不要宣称已经实现完整的结构化长期记忆系统。[R21]

## 10 模型接入与 SSE 体验

### 模型网关的实际职责

Gateway 用 httpx 调用兼容的 `/chat/completions` 和 `/embeddings`，为回答和摘要构建消息。当前 `route` 返回配置的同一个聊天模型，provider 文本固定为 deepseek；`task_type` 进入 reason，不是动态选模型算法。设置中存在其他模型字段，也不能证明该字段被运行路径使用。[R05]

模型名称和 API 版本属于部署配置，面试应说“支持兼容 API 的可配置接入”，并说明改变 provider 还需要验证鉴权、协议、工具能力和错误处理。当前没有完整的多模型路由、服务端模型 fallback、prompt cache 或调用费用统计。

### 为什么用 SSE

前端发一次请求，后端持续推送阶段和结果，方向主要是服务端到客户端，适合使用 SSE。项目需要 POST JSON 与 Authorization 请求头，所以前端使用 fetch 读取 ReadableStream，自己解析事件，而不是直接使用浏览器 EventSource。[R20]

SSE 事件以空行分隔，event 指定类型，data 承载 JSON。前端要处理一次网络 chunk 包含多个事件，或一个事件被拆成多个 chunk 的情况。当前代码使用 TextDecoder、buffer、换行规范化和类型过滤。未知或坏格式事件会被忽略，适合增强协议容错；也意味着需要额外监控才能发现事件解析丢失。[R20] [SSE 格式说明](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)

| 事件 | 用户或前端得到什么 |
| --- | --- |
| status | 正在加载历史、检索、计划或生成等阶段 |
| plan | 计划是否用工具、工具名、参数、原因与确认要求 |
| tool_call | 一个即将处理的工具调用 |
| tool_result | success、failed 或 timeout 以及摘要和错误 |
| token | 可展示的回答文本片段，当前通常是完整清理后的文本 |
| done | 会话 ID、引用、工具结果、trace 与模型路由 |
| error | 运行异常及可用的 trace 信息 |

### 全量缓冲为什么影响首字延迟

Gateway 确实接收上游 token 流，但 Runtime 的 `_generate_answer` 把它们收集到 response_parts，拼成完整文本后统一移除 DSML、tool_calls 和 invoke 等内部协议标记，再发送。这能避免标签被跨 chunk 拆开后泄露到界面和历史消息。[R02] [R09]

代价是可见首字时间包含检索、规划、工具和整段回答生成时间。SSE 状态能告诉用户仍在工作，却不等于答案已经逐字出现。改进可以采用有状态流式过滤器，缓冲可能属于标签的短前缀，只放行已确认安全的普通文本；需要跨 chunk、畸形标签和截断响应测试。

### 确认界面稳定和持久恢复

前端在请求结束时检查 pending confirmation，保留当前内存中的 trace，并避免会话刷新逻辑覆盖确认按钮。`isStreaming=false` 只代表网络阶段结束，并不代表业务不再等待确认。[R20]

但是首次确认拦截路径没有保存 assistant 消息，前端 ref/state 也不是数据库。浏览器硬刷新后，待确认信息缺少可靠持久来源。当前确认请求没有校验一次性审批 ID、参数摘要、有效期和已消费标记；重复提交也没有完整的执行去重。下一步应持久化 pending action，并把确认和精确参数绑定。[R01] [R04] [R20]

## 11 后台任务和调度可靠性

### 为什么要单独建任务系统

用户希望执行耗时 prompt，或每天运行一次相同请求时，浏览器连接不适合作为工作的唯一生命线。任务表保存输入、状态、进度、重试时间和结果；Scheduler 负责找到可运行任务；Runner 执行并更新记录。前台和后台共享 AgentRuntime，所以工具仍走同一个 Executor。[R12] [R13] [R15]

`POST /api/tasks/agent` 创建 agent 类型任务，通常返回 HTTP 202；`POST /api/tasks` 创建的是手动任务记录，不能把两个接口都理解成会立即让模型开始工作。

### 状态转换和恢复

```text
queued → running → succeeded
           ├─ 可重试失败 → queued → 下一次 running
           ├─ 不可重试或达到上限 → failed
           ├─ 用户取消 → cancelled
           └─ 租约过期 → 有余量则 queued  否则 stale
```

公开 PATCH 接口的状态转换表用于约束客户端变更；Runner 和恢复函数内部会直接执行 running 到 queued 的重试转换，不完全依赖这张公开转换表。终态通常不允许通过普通接口重新转回运行。[R12] [R13] [R16]

### 抢占和租约如何配合

Runner 用数据库行锁 `FOR UPDATE SKIP LOCKED` 获取 queued 任务；竞争 worker 跳过被锁记录。获得任务后写 running、attempt_count、heartbeat_at 和 lease_expires_at 并提交，释放数据库锁。此后通过有时限的租约表明“有人正在执行”。[R12]

默认心跳 15 秒，租约 180 秒，后台并发 2，维护扫描 5 秒。活跃租约不会被随意重置；过期后如果仍有尝试次数则重新排队，最后一次也耗尽时标记 stale。恢复并非精确在第 180 秒发生，还受最后一次续约、扫描和排队影响。[R12] [R13] [R22]

面试中的直观解释是：“行锁解决领取时的竞争，租约解决领取之后进程消失的问题。不能一直拿着数据库行锁等模型返回，所以提交领取状态后，用心跳续约表达存活。”

### 重试和取消

重试默认最多 3 次尝试，不是首次加 3 次。退避为 `min(base × 2^(attempt_count-1), 3600 秒)`，base 默认 5 秒。授权、缺 key、参数和确认等已知错误不重试；网络或 provider 临时错误可重试，未知错误也受次数限制重试。当前分类主要匹配错误文本，并没有完善的 typed error taxonomy 或随机抖动。[R12]

等待重试期间先退出 Semaphore，再 sleep，不占模型执行并发名额。Runner 的任务进度 session、Runtime session 和心跳 session 分开，避免把一个 AsyncSession 交给并发协程共享。[R12] [R13]

取消接口先把状态写为 cancelled，再通知本进程调度器取消 asyncio task。Runner 在写终态前 refresh，防止覆盖已取消状态。服务关闭导致的取消会重新排队并退回本次尝试计数；用户取消保持取消。多进程时，本进程内的 task.cancel 不能直接中断另一进程 worker，执行中止与状态取消仍需完善协调。[R12] [R15]

### 定时任务如何去重

TaskSchedule 支持 once、interval、daily。每日计划使用 IANA 时区，存储和比较执行时间统一为 UTC；一次性计划要求未来且带时区的时间；间隔最低 60 秒。调度器锁住到期计划，创建任务并推进 next_run_at，在同一事务提交。[R14]

同一计划同一次发生的幂等键是 `schedule:<schedule_id>:<UTC occurrence>`，任务表有 `(user_id, idempotency_key)` 唯一约束。手动 Run now 使用随机键，表示用户主动新增一次执行，不改变原计划的正常下一次时间。[R14] [R16]

错过多个间隔时，代码创建当前到期记录的一次任务，再把 next_run_at 推到未来，避免把所有漏跑时间补成一批任务。时区支持不等于已经定义并充分测试夏令时重复和不存在时间的所有策略。

### 不要声称 exactly once

“同一个请求不重复创建任务”“两个 worker 不同时领取同一 queued 行”“外部动作只发生一次”是三个不同保证。前两者有实现机制；后者仍有工具成功后、结果提交前崩溃的空窗，重试可能再次执行该工具。[R12] [R15]

当前任务没有 lease owner 或 fencing token 来拒绝旧 worker 的迟到写入，恢复函数的扫描与心跳也不是严格的跨进程协调协议。并发 Semaphore 每个 Scheduler 实例独立，多进程总并发可能放大。更准确的描述是“具备任务级恢复和有限并发控制，尚需加强分布式执行保证”。

后台遇到普通交互确认会失败并引导回聊天，不会无限等待无人审批。但搜索结果增强抓取存在 Runtime 的自动确认例外，所以不能笼统承诺“所有后台网络调用都需要人工逐次确认”。[R02] [R12]

## 12 数据建模和应用安全

### 核心数据关系

| 表 | 主要信息 | 与其他数据的关系 |
| --- | --- | --- |
| users 与 user_sessions | 用户、密码哈希、会话 token 哈希 | 用户拥有会话、文档、任务与计划 |
| conversations 与 messages | 会话与消息正文、模型、metadata | 一个会话有多条消息 |
| memory_summaries | 摘要与有效时间 | 属于一个会话 |
| knowledge_documents 与 knowledge_chunks | 文档来源、片段、向量 | 一个文档有多个片段 |
| tool_calls | 实际工具参数、状态、摘要、耗时 | 可关联用户、会话和任务 |
| tasks | 输入、状态、结果、尝试与租约 | 可绑定会话和定时计划 |
| task_schedules | prompt、时区、执行规则和下一次时间 | 按时产生任务 |

PostgreSQL 把关系数据、JSONB 元数据和向量放在一个存储中，减少个人项目的运维组件。消息正文适合普通列，变化较多的工具元数据适合 JSONB。外键、唯一约束和事务用于表达关系与竞争条件；JSONB 也需要控制体积和版本，不能无限追加。[R16]

代码有 messages 的会话与时间复合索引，以及知识片段的文档与序号索引。Alembic 有 8 个版本迁移文件，包含用户归属、工具审计和任务可靠性扩展。没有单独的 Elasticsearch、Redis、Celery 或向量数据库服务。[R16] [R23]

### 两层身份控制

应用访问口令 `x-agent-access-token` 是可选的额外入口限制；用户登录后的 Bearer token 用来识别具体用户。二者目的不同。`/api/auth/status` 只反映是否设置了应用口令，不能证明数据库可写、用户可注册或模型服务可用。[R17] [R18]

密码采用带随机 salt 的 PBKDF2-HMAC-SHA256，当前迭代数 260000；会话 token 使用随机值，数据库保存 SHA-256 哈希，查询时比较 token 的哈希。当前不是 JWT，也没有现成的会话过期、撤销和完整登录限速机制。[R18]

### 授权应当在哪里做

知道一个 conversation_id 不代表有权访问它。路由会把对象 ID 与 current_user.id 一起查，查询知识时也以用户和会话作用域过滤。摘要通过所属会话验证归属。测试需要覆盖“用户 A 不能读写用户 B 的资源”，不应只测未登录时的 401。[R04] [R10] [R19] [R21]

当前隔离主要在应用查询条件层完成，没有数据库 RLS 作为额外防线。数据库 session 也不是一次聊天的大事务：用户消息、审计和回答分阶段提交，失败时保留部分记录有利于追踪，但需要产品能解释未完成状态。[R02] [R08]

### 面向部署的实际改进

本地开发的 CORS 默认正则较宽，前端 token 放在 browser storage；上线应收紧来源、加强 XSS 防护与 token 生命周期。文件工具的 workspace resolve 和保护目录检查是路径边界的一部分，但不代表任意秘密文件都能被识别；审计参数还应脱敏。[R11] [R18] [R20] [R26]

这些是面向扩大使用范围的改进项。面试不需要把个人应用包装成已经完成企业多租户安全认证的平台。

## 13 测试证据与可讲的技术难点

### 本次实际执行的检查

检查日期为 2026 年 9 月 7 日，工作目录是 test worktree，代码基线为首页标注的提交。后端使用本机已有项目虚拟环境，前端使用已有依赖。

| 检查 | 本次结果 | 结果能说明什么 |
| --- | --- | --- |
| 后端 pytest 默认集合 | 215 passed，1 deselected，19.04 秒 | 当前确定性回归测试通过，live 测试按默认标记排除 |
| 离线 Agent 评测 | 12 条全部通过，门槛 100% | 预定义规划、协议过滤、引用上下文与关键词规则符合预期 |
| Ruff | All checks passed | 当前启用的静态检查通过 |
| 前端 Vitest | 4 个文件，13 条全部通过 | 事件解析、trace 与任务等抽取逻辑测试通过 |
| 前端构建 | TypeScript 与 Vite build 成功 | 当前代码可完成类型检查和生产构建 |

后端测试结束出现 Windows 临时目录清理警告，测试本身退出码为 0。本次未运行付费或账户绑定的 live 模型测试，也未实测吞吐、用户规模或实际回答准确率。检查时 8000 与 5173 未发现监听服务，因此没有把真实浏览器聊天、注册或工具确认流程记为本次已通过。

CI 文件设置了后端 coverage 至少 80% 的门槛，并运行 Ruff、离线评测、前端测试、构建及依赖审计。本次只运行了表内检查，没有测得新的 coverage 数值，也没有把 CI 配置本身当成最新远端 CI 成功证据。[R23] [R25]

### 为什么通过率不是模型准确率

`EvalGateway` 是离线替身，12 条场景比较规划结果、输出片段和关键词，不调用真实大模型完成开放式任务。其中 citation_quality 检查的是上下文是否保留来源信息，不是在衡量最终回答的事实正确率。[R25]

合适的表述是：“我用确定性测试防止明确行为回归；对真实模型还需要固定题集、多次运行、人工标注或经校准的裁判模型，分开统计工具选择、任务完成、引用正确性、成本和延迟。”

### 三个可以深入讲的案例

**案例一 URL 确认出现得太晚。** 触发条件是用户只想读一个 URL，但系统先做上下文检索，确认按钮迟迟不出现。当前图增加 preflight_confirmable_tool，在正常检索之前识别需确认的普通 Fetch 请求，经 Executor 产生 blocked 结果，再交由界面显示。相关测试检查了计划发出前没有进入 retrieving_context，并确认首次拦截不会保存 assistant 消息。收益是把人工决策更早暴露，代价是新分支要单独考虑待确认状态持久化。[R01] [R27]

**案例二 模型吐出内部工具标记。** 模型可能把 DSML 或 invoke 结构当作普通 content 返回；直接转发会污染用户界面和历史。当前先收集再清理，测试同时检查输出事件与保存消息不包含这些标记。由此带来的全量等待，是解释可靠性与首字体验取舍的好例子。[R02] [R09] [R27]

**案例三 后台任务失败后如何继续。** 网络错误不能永久卡在 running，但也不能在每次重启时把仍有活跃 worker 的任务重置。当前通过心跳和租约判断是否被遗弃，用有界重试重新排队；退避期间释放并发名额。相关测试覆盖活跃租约、过期恢复、可重试错误、认证失败与关闭中断。[R12] [R13] [R28]

讲难点时用“现象—原因—实现—验证—剩余风险”的顺序。以上描述的是仓库中的问题与实现，不自动证明你亲历了每一步；按真实经历选择最熟悉的案例，不编造耗时、收益百分比或团队规模。

### 简历项目描述参考

可以在与实际贡献一致的前提下使用下列描述：

- 基于 FastAPI、React 与 LangGraph 实现个人 Agent 应用，支持会话持久化、规则驱动的工具选择及 SSE 执行状态展示。
- 设计统一工具注册与执行链路，接入本地插件与 MCP，处理参数检查、用户确认、超时、错误规范化和工具调用审计。
- 使用 PostgreSQL 与 pgvector 实现文档分块检索、引用返回和会话摘要，支持 embedding 不可用时的关键词降级。
- 实现后台 Agent 任务与定时调度，加入幂等键、数据库抢占、心跳租约、有界重试和取消状态管理。

## 14 高频追问与回答示例

本章适合口头练习。先用 20 至 40 秒说核心答案，再根据面试官兴趣展开。不要把整页一次背给对方。

### Q01 你的 Agent 和普通聊天接口有什么区别

它在模型前后增加上下文恢复、检索、工具计划、受控执行、结果反馈和持久化，还支持后台任务。普通聊天转发主要处理消息与模型响应。本项目的自主程度有限，规划以规则为主，但执行和审计链路已实际存在。

### Q02 项目中哪些部分属于大模型能力

回答生成、会话摘要、embedding 和可选的搜索 query 规范化会调用模型。路由判断、权限、参数校验、任务状态、租约和数据库事务主要由确定性代码完成。可靠性不能寄希望于模型自觉遵守提示词。

### Q03 为什么使用 LangGraph

这里有正常回答、工具循环、确认分支和统一收尾，图结构能把顺序和状态转移显式写出来。当前规模也能用普通循环实现；引入图的收益主要在可读性与后续扩展，不能把选了框架本身当作复杂度或效果优势。

### Q04 已经实现 ReAct 了吗

有计划、动作、observation 和再计划的外形，但当前计划不是通用的模型推理循环，也没有持续依据 observation 生成新行动的规划器。更准确地说是“有界、规则主导的 Agent 工作流”。我可以说明升级模型规划还需要哪些协议与控制。

### Q05 为什么不让模型自己决定所有工具调用

工具路由、权限和副作用必须有稳定边界。对明确 URL 等场景，规则可减少不必要的规划成本和错误。对于开放任务可以让模型提出计划，但仍必须经过后端校验和用户授权。策略可以逐步引入，而无需放弃统一执行层。

### Q06 Function Calling 与 MCP 能相互替代吗

不能简单替代。前者是模型提出工具调用的接口能力，后者是应用连接工具服务的协议。可以先通过 MCP 发现 schema，转成模型工具描述，再把模型产生的调用送入 Executor，最终由 MCP Client 请求外部服务。当前尚未完成这条原生模型调用链。

### Q07 如何避免 Agent 无限循环

当前默认最多 3 轮图内工具执行，并对常见工具类别设置已经调用过的停止条件。未来通用规划需要增加参数归一化后的调用去重、总调用次数、时间和 token 预算，以及失败次数上限。搜索增强也要计入总预算，不能只看图轮数。

### Q08 为什么确认不能只做一个前端按钮

攻击者或异常客户端可以直接请求后端。当前 Executor 会检查 confirmed，但确认接口本身还缺持久审批凭据。完整方案应由后端保存工具与精确参数，发一次性审批 ID，确认时核对用户、会话、参数摘要、时效和消费状态。

### Q09 LangGraph 的 thread_id 能自动恢复吗

不能。它是状态命名或定位信息，真正恢复需要保存 checkpoint。当前图未配置 checkpointer；confirmed_tool 是新请求重新建图，后台恢复也是重新执行任务。我会把业务记录持久化和图节点恢复分开说明。

### Q10 为什么工具失败也要写数据库

失败同样是执行事实。缺参数、未确认、超时和 provider 失败的处理方式不同，审计能帮助前端解释状态和定位问题，也能为重试判断提供证据。当前开始与结束记录仍可进一步完善，尤其要处理进程崩溃的空窗。

### Q11 HTTP 200 为什么还可能是工具失败

HTTP 200 只说明请求协议层有正常响应。MCP 工具可能在响应中设置 isError=true。本项目把这种结果统一成 failed，保持前端、数据库和模型 observation 的语义一致。

### Q12 给工具加 timeout 就不会有副作用了吗

不能这样保证。同步 handler 在工作线程里执行，等待超时不能证明线程或远端动作停止。要对副作用操作加入幂等键、事务或补偿，并对需要强隔离的工具使用独立进程或受控执行环境。

### Q13 模型看到恶意网页指令怎么办

网页和工具返回应作为不可信材料，不能改变系统权限。当前有工具描述过滤、部分 URL 策略和执行确认，但不能保证彻底抵御 prompt injection。进一步要区分指令与证据来源、限制工具权限，并做恶意文档与数据外传场景评测。

### Q14 你的 SSRF 防护有哪些不足

当前 Fetch 校验协议、已知本地主机和字面私有 IP，没有在这一层校验域名解析结果或每次重定向。下一步补 DNS 与重定向验证、连接时地址约束及网络出口策略，并把检查复用到所有相关外部读取路径。

### Q15 RAG 和微调有什么区别

RAG 在请求时检索外部证据，不改模型权重，适合经常更新、需要溯源的知识。微调调整模型参数，更适合稳定的风格、格式或任务行为。项目需求主要是个人文档查询，因此先选择可更新、可引用的知识检索。

### Q16 embedding 是什么 为什么需要同一模型

embedding 把文本映射为固定维度向量，用距离近似语义关联。文档和 query 必须使用兼容的向量空间；即使两个模型输出维度一样，向量含义也未必可比较。更换模型通常需要重建存量向量，并重新校准阈值。

### Q17 为什么选 900 字符和 120 重叠

这是当前实现的默认工程参数，便于得到较小片段并保留边界上下文，并没有充分实验支持它最优。实际选择应依据资料结构、语言、embedding 输入限制和检索评测比较，必要时改成按标题、句子或 token 分块。

### Q18 你的检索已经是混合检索吗

当前是级联降级：向量有结果就直接返回，否则关键词，再考虑会话文档回退。真正多路融合可以同时取向量和关键词候选，再归一化或用排名融合，最后重排。不能直接把命中词数与余弦分数相加。

### Q19 为什么不直接使用专用向量数据库

项目本就需要 PostgreSQL 保存关系与任务数据，pgvector 让检索与权限元数据放在一起，降低部署复杂度。若数据规模、索引需求或服务隔离要求增长，再用基准测试比较专用方案，当前没有必要宣称一种数据库永远更优。

### Q20 HNSW 和 IVFFlat 你用了哪个

当前都没配置。知道 pgvector 支持这些索引，不代表项目已部署。若面试官继续问，我会解释 ANN 是用部分召回损失换速度，并通过同一题集测 recall 与 p95 延迟，而不是虚构已有索引调优结果。

### Q21 如何判断 RAG 回答有没有幻觉

把回答拆成可核对的事实，看每个事实是否被检索证据支持，引用是否对应，以及证据缺失时是否承认不确定。检索命中和最终回答正确应分开测；有引用列表本身不代表回答忠实于来源。

### Q22 文档里的 Indexed 表示什么

当前表示已经走过入库流程，但 embedding 异常会降级为空向量，状态仍可能显示 indexed。因此不能仅凭状态判断全部向量有效。可以补充 embedding_status、失败原因、完成片段数和后台重建任务。

### Q23 会话摘要会不会越总结越错

会有压缩遗漏和错误累积风险。当前保留消息原文并提供摘要管理入口，但摘要仍由模型生成。可改成增量摘要、来源消息引用和用户可确认的结构化事实，并让近期明确表述优先于旧摘要。

### Q24 怎么控制上下文长度和成本

当前有消息条数、chunk 数、工具输出字符数与轮数限制，但缺整体 token 预算。下一步应先测各部分占用，再分配给历史、摘要、检索与工具；记录每次调用 usage、模型价格配置和延迟，才能谈成本优化效果。

### Q25 你实现的是逐字流式输出吗

当前模型上游是流式，运行状态通过 SSE 到前端；答案会完整收集并清理后发出，所以通常不是逐字显示。这是为了防内部工具标记泄露做的取舍。可以用流式状态机过滤改进，但要补跨 chunk 的协议安全测试。

### Q26 为什么不用 WebSocket

聊天请求主要是一条上行输入和持续下行事件，SSE 加 fetch 足够表达，并能沿用 HTTP 认证。若需要实时语音、频繁双向控制或多路复用，再评估 WebSocket。当前也没有实现 SSE 事件序号重放和断点续传。

### Q27 后台任务的进度百分比准确吗

它是根据阶段映射出的粗略进度，例如规划、工具和生成分别对应不同值；并不预测剩余秒数。前端应把它作为状态提示。未知长度的模型生成阶段，展示当前工作和已等待时间通常更可解释。

### Q28 任务为什么既要行锁又要租约

行锁保护 queued 到 running 的短事务领取，提交后释放，避免长期占锁。租约描述领取后的执行有效期，心跳续约。worker 崩溃后不再续约，调度器据此重新排队或标记 stale。两者处理的是不同时间段的问题。

### Q29 幂等键已经能保证工具不重复吗

任务表的唯一键主要避免重复创建任务，不能覆盖外部动作完成后本地尚未提交的崩溃窗口。写入型工具还需要动作级幂等键，或可查询结果、补偿和 outbox 等设计，并说明哪些外部服务支持这些机制。

### Q30 多个 worker 会不会突破并发限制

当前 Semaphore 在单个 Scheduler 实例中生效，每个后端进程都有自己的限制；增加进程会增加总并发。数据库抢占减少同一任务重复领取，但不等于全局配额。规模扩大要设计共享配额或独立 worker 集群。

### Q31 为什么重试不占并发名额

退避是在等待，不是在调用模型。如果持有 Semaphore 再等待，少量失败任务就可能阻塞健康任务。当前 _run_job 离开受限区域后再 sleep，让其他排队工作使用执行名额。

### Q32 任务取消后工具是否肯定停止

状态会先写 cancelled，本进程 worker 会收到取消，保存结果前也会刷新状态。但已在外部执行的动作、同步线程和其他进程 worker 不一定立即停止。应区分“用户撤销任务”“停止后续步骤”和“撤销已经发生的副作用”。

### Q33 为什么不用 Celery 或消息队列

当前个人规模下，数据库持久记录加进程内调度能减少部署组件，并验证任务状态模型。负载和可靠性要求提高后，可以拆独立 worker 或消息队列；迁移时仍要保留幂等、取消、重试和工具审计，队列不会自动解决全部语义。

### Q34 215 条测试能说明项目质量很好吗

能证明当前测试覆盖的行为符合断言，不能独立证明生产稳定性。许多测试使用 FakeSession、FakeGateway 或 mock 服务，不等于真实数据库、真实模型和浏览器端到端验证。数量之外要看边界场景和失败路径是否有代表性。

### Q35 面试官问性能提升了多少怎么回答

如果没有实验记录，就明确说没有可引用的基准。随后说明准备如何测：固定模型、文档量、题集、并发和缓存状态，记录首事件、首可见答案、总耗时、p50/p95 与失败率，做前后对照。不要用测试耗时替代在线服务性能。

### Q36 如果现在只能优先改三件事

先补持久化审批与准确参数绑定，再引入真实任务题集和 token/延迟统计，然后按测量结果升级规划或检索。若岗位特别关注通用 Agent，可把原生结构化工具调用作为第三项，但仍保留执行边界，不直接让模型触发动作。

### Q37 这个项目最大的不足是什么

我会选择自己最能解释的一点，例如“当前规划规则多、泛化弱”。然后给出触发条件：用户要求先查多个资料再比较，当前可能只做一次某类工具。再说明升级路径、成本和验收方式，而不是只说项目还可以优化。

### Q38 你在 AI 辅助编程中真正做了什么

如实说明你定义需求和架构边界、审查生成代码、复现错误、编写或理解测试、验证结果的工作。能现场解释一个函数、一个失败用例和一个取舍，比声称每行都手写更有说服力。没有做过的事情直接说明并展示现在的理解。

### Q39 让你现场扩展一个工具怎么办

先给出 manifest 的名称、参数 schema、权限、确认、超时和输出策略，再实现 handler 或 MCP 适配，注册到统一工具表。然后测试非法参数、权限拒绝、超时和异常。最后接 Planner 或显式调用入口，检查 trace 和审计，避免单独开一条绕过 Executor 的快捷路径。

### Q40 怎样设计一次真实 Agent 评测

按知识问答、URL、搜索、复合工具任务、拒绝和恢复场景分组，定义可观测完成标准。固定配置，多次运行并保存完整 trace；分开记录任务成功、工具选择、参数正确、引用支持、确认违规、耗时和成本。先人工标注一部分样本，再验证自动评分是否可靠。

## 15 五天准备计划和演示脚本

### 每天都要交出可检查的结果

建议每天安排约 3 小时专注准备。时间不够时优先完成验收目标，再减少泛读。每次口述录音后，只修正三类问题：说错实现、讲不清因果、回答超出自己的证据。

| 日期 | 主题与练习 | 当天验收 |
| --- | --- | --- |
| 9 月 7 日 | 读第 02 至 05 章，画图，打开 graph、nodes 与 runtime；练 30 秒和 2 分钟介绍 | 不看文档讲清一次聊天路径，并说对规则规划和无 checkpoint |
| 9 月 8 日 | 读第 06、07、10 章，追一次本地读文件与 URL 确认；练 Q05 至 Q14、Q25 至 Q26 | 讲清 ToolExecutor 的检查顺序、两段确认流程和 SSE 缓冲取舍 |
| 9 月 9 日 | 读第 08、09、12 章，用短文档解释 chunk、向量、关键词和摘要；练 Q15 至 Q24 | 手写检索与记忆两条链，区分混合检索与降级、消息数与 token 数 |
| 9 月 10 日 | 读第 11、13 章，画任务状态图，解释重试、租约、幂等与取消；练 Q27 至 Q35 | 用崩溃案例说明为何不是 exactly once，解释 per-process 并发 |
| 9 月 11 日 | 做 30 至 40 分钟模拟面试，录两分钟介绍，演练五分钟演示，整理个人贡献 | 随机抽 10 题至少 8 题准确，三个难点中至少两个可结合代码展开 |

每天最后 15 分钟复习上一日内容。第四、第五天不要为了增加亮点做大改动，优先保证演示可重复和解释准确。

### 自评分标准

每题 0 至 3 分：0 分是无法解释；1 分是只会术语；2 分是能解释当前流程并给例子；3 分是还能指出代码位置、失败场景与取舍。40 题满分 120；建议至少达到 90 分，且 Q04、Q06、Q09、Q18、Q25、Q29 不出现实现性错误。这个分数是自查目标，不是面试通过率预测。

### 五分钟现场演示

先确认运行 worktree、数据库、模型配置和工具加载情况。只展示不敏感资料，并提前准备相同输入的备用结果记录。每一步都要能从 UI 连接到后端流程。

1. **资料问答约 90 秒。** 上传一份很短的 Markdown：例如“海棠项目缓存 TTL 为 60 秒，执行租约为 180 秒”。问“资料中的缓存 TTL 是多少，依据是什么”。展示引用中的文档名和片段，强调数字来自文档。
2. **短期记忆约 45 秒。** 告诉系统“本次回答请使用简短中文”，再追问刚才的资料。展示近期消息上下文，说明这一步不是证明长期摘要已经生成。
3. **工具确认约 90 秒。** 用已配置且可访问的普通 URL 请求摘要，先展示计划、参数和确认；确认后看结果与回答。再指出 ToolExecutor 和 confirm 路由。现场不要把“浏览器刷新可恢复审批”列为已完成演示。
4. **后台任务约 60 秒。** 提交一个不依赖人工确认的简单总结任务，展示 queued、running、结果和事件。若配置每天执行，解释计划如何产生任务即可，不需要现场等待一天。
5. **验证和边界约 15 秒。** 展示测试结果摘要，并说明规则规划、答案缓冲与任务级恢复的边界。

演示资料里的 TTL 是人为构造的练习数据；不要误当作 AgentDemo 的运行配置。后台心跳、租约等项目默认值应以第 11 章为准。

### 环境检查和回归命令

在实际项目根目录先执行以下只读检查，确定代码和服务是否一致：

```powershell
git worktree list
git branch --show-current
git status --short --branch
Get-NetTCPConnection -State Listen -LocalPort 8000,5173
```

若端口有监听，再查对应进程的可执行程序和工作配置，确认后端与前端实际指向哪个 worktree。不要看到 run 分支有代码就默认 live 服务加载了那份代码。进程参数可能包含敏感信息，展示前先检查并脱敏。

项目虚拟环境准备好后，在 backend 目录可运行：

```powershell
python -m pytest -q
python -m app.evals
python -m ruff check app tests
```

在 frontend 目录运行：

```powershell
npm test
npm run build
```

这里的 python 应指向已安装项目依赖的虚拟环境。默认 pytest 排除了 live 测试，付费或账户绑定测试需要单独配置并明确选择。真实注册和登录还要验证数据库写路径，健康检查不能代替它们。

### 演示失败时如何解释

| 现象 | 优先排查 | 对面试官的准确说明 |
| --- | --- | --- |
| 网页打不开 | 5173 监听、Vite proxy 与 backend 目标 | 前端服务或代理连接问题，先确认服务位置 |
| 能到登录页但注册失败 | PostgreSQL、迁移、真实写入错误 | 页面可达不代表数据库可用 |
| 模型没返回回答 | key、base URL、模型配置与服务响应 | 模型 provider 未就绪，不能据此判断图结构错误 |
| 一直停在检索 | embedding 与 MCP 上下文读取耗时 | 依次定位外部等待，并核对是否应该走 URL 前置确认 |
| 工具显示 timeout | 实际工具调用耗时、manifest 与全局 timeout | 等待已超时，不能保证外部动作没有发生 |
| 引用没找到答案 | 文档归属、向量状态、关键词候选与阈值 | 分开检查检索失败和生成失败 |

### 还需要向自己确认的个人事实

面试前写下一段真实贡献说明：哪些模块由你设计或实现，哪些借助 AI 或参考资料；你亲自复现过的一个错误；你做过的验证；如果重做，会改变哪个设计。这些内容无法仅靠仓库推断，也不应从示例直接复制成个人经历。

## 16 面试前最后一小时速查

### 十个事实

1. 定位是本地优先个人 Agent 应用，远端模型仍可能收到上下文。
2. 核心入口是聊天 SSE 与确认续跑两个 POST 接口。
3. LangGraph 管执行流程，当前没有 checkpointer。
4. 当前 Planner 主要是规则与工具名匹配，没有原生 Function Calling。
5. 工具统一经过 Executor；确认、校验、超时、标准结果与审计是主线。
6. 普通 URL 确认被前置；搜索结果 Fetch 当前有自动确认例外。
7. RAG 默认 900 字符、120 重叠、top 4、阈值 0.45、向量 1536 维。
8. 向量无有效结果才走关键词；不是 BM25、多路融合或 reranker。
9. 短期窗口默认 12 条消息；摘要仍按会话，刷新会读全部历史。
10. 后台默认并发 2、最多 3 次尝试、心跳 15 秒、租约 180 秒；任务恢复不是 exactly once。

### 五句容易加深追问的表达

“这个保证只到任务创建层，外部副作用还要独立幂等。”

“目前代码是这样处理的，下一步我会用这个失败场景验证改进。”

“这里的 100% 是 12 条离线场景通过率，不是真实回答准确率。”

“上下文和工具结果是证据输入，权限仍由执行层决定。”

“这项能力我没有实测数据，所以不会给出性能提升数字。”

### 三个不能混淆的边界

持久保存消息不等于图节点恢复；网络响应成功不等于工具业务成功；显示引用不等于每个回答事实都得到证据支持。

### 临场答题顺序

先回答结论，再用一次具体请求解释流程，补上当前代码位置，最后讲一个限制或取舍。遇到没实现的能力，清楚地区分“现在如何工作”和“如果扩展会怎样设计”。比起覆盖所有术语，把最熟悉的两条链路讲透更重要。

## 17 代码证据与推荐阅读入口

以下链接固定到分析基线提交，后续分支变化不会让本手册的证据漂移。先读对应函数，再看同主题测试；不要仅从文件名推断功能深度。

| 编号 | 阅读入口 | 学习重点 |
| --- | --- | --- |
| R01 | [LangGraph 图与节点](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/agent/graph.py#L12) | 确认分支、循环、compile；另读 nodes.py |
| R02 | [AgentRuntime](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/agent/runtime.py#L161) | stream、规划、输出缓冲、搜索增强与摘要 |
| R03 | [图状态](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/agent/state.py#L8) | TypedDict 字段与节点间状态传递 |
| R04 | [聊天路由](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/api/routes/conversations.py#L104) | 聊天和确认接口及会话归属 |
| R05 | [模型网关](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/services/model_gateway.py#L25) | 名称匹配、HTTP payload、消息、embedding 与摘要 |
| R06 | [本地插件注册](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/services/plugin_registry.py#L55) | manifest、动态导入和 RegisteredTool |
| R07 | [统一 MCP 工具注册](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/mcp/registry.py#L8) | 另读同目录 client.py、tools.py 和 config.py |
| R08 | [工具执行边界](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/services/tool_executor.py#L21) | run、参数子集、超时、isError 与审计 |
| R09 | [回答清理与降级](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/agent/response_policy.py#L23) | 协议标记、搜索失败和工具结果回退 |
| R10 | [RAG 服务](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/services/rag.py#L97) | index_text、search、作用域、关键词与分块 |
| R11 | [MCP 安全](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/core/mcp_security.py#L23) | 权限、确认、URL 校验和配置脱敏 |
| R12 | [后台任务执行](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/services/agent_task_runner.py#L79) | 抢占、session 分离、重试、心跳和取消 |
| R13 | [后台调度器](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/services/task_scheduler.py#L26) | Semaphore、维护扫描和租约恢复 |
| R14 | [定时发生计算](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/services/task_schedules.py#L30) | 时区、到期抢占、幂等键和错过间隔处理 |
| R15 | [任务路由](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/api/routes/tasks.py#L79) | 任务类型、202、幂等冲突和取消 |
| R16 | [任务与关系模型](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/models/task.py#L66) | 唯一约束；另读同目录 conversation、knowledge、tool、user 和 task_schedule |
| R17 | [应用启动](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/main.py#L50) | 插件与 MCP 发现、调度器、middleware 和路由 |
| R18 | [登录与身份](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/api/routes/auth.py#L31) | 另读 core/security.py 的密码和 token 哈希 |
| R19 | [知识上传路由](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/api/routes/knowledge.py#L97) | 所有权、文件读取与文本 PDF 提取 |
| R20 | [前端 SSE 客户端](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/frontend/src/api.ts#L267) | 另读 App.tsx 的确认状态和 features/runtime/runtimeTrace.ts |
| R21 | [摘要管理](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/api/routes/memory.py#L17) | 归属验证、禁用和删除 |
| R22 | [默认配置](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/core/config.py#L8) | 消息窗口、超时、模型和任务默认值 |
| R23 | [CI 质量流程](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/.github/workflows/quality.yml#L1) | Ruff、离线评测、coverage 门槛和前端检查 |
| R24 | [搜索适配服务](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/services/web_search.py#L23) | provider 分离、disabled、mock 与 HTTP 实现 |
| R25 | [离线评测器](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/evals/runner.py#L20) | 替身、评分规则；题集见 scenarios.json |
| R26 | [本地读文件插件](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/plugins/read_file/tool.py#L19) | workspace 约束、保护目录与输出截断 |
| R27 | [Runtime 回归测试](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/tests/test_agent_runtime.py#L556) | 协议清理、URL 确认、图契约与记忆 |
| R28 | [后台任务回归测试](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/tests/test_agent_task_runner.py#L126) | 另读 test_task_scheduler.py 与 test_task_schedules.py |
| R29 | [MCP 服务端适配层](https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/mcp/server.py#L16) | 工具 façade；另读 api/routes/mcp.py 的实际路由 |

### 代码与旧说明需要区分的地方

| 主题 | 应当以代码为准的结论 |
| --- | --- |
| README 对工具规划的概括 | Gateway 的通用规划方法当前只是名称匹配，不能推导出模型自主规划 |
| README 的 token by token 表述 | Runtime 先缓冲完整回答，再清理和发出 token 事件 |
| 使用 thread_id 的配置 | graph.compile 未传 checkpointer，不能据此称为 LangGraph 断点恢复 |
| MCP Server 介绍 | 当前有 façade 和 schema 适配，未在主应用挂出完整 MCP 传输服务 |
| 确认 UI 的稳定性要求 | 内存状态可在部分刷新逻辑中保留，但待审批数据尚未完整持久化 |
| 网络工具确认原则 | 搜索增强路径在 Runtime 中明确传 confirmed=True，是已实现的例外 |
| 可靠任务与幂等 | 领取和创建去重已有机制，外部动作去重和旧 worker 隔离仍待完善 |

### 推荐的后续改进及验收方式

| 优先项 | 具体改动方向 | 有意义的验收 |
| --- | --- | --- |
| 持久审批 | 保存 pending action、参数摘要、一次性审批 ID 与消费状态 | 硬刷新可恢复，篡改参数被拒绝，重复确认不重复动作 |
| 真实 Agent 评测 | 建题集、来源标注、完整 trace 与多次模型运行 | 能按类别报告成功率、引用支持、延迟和成本 |
| 通用工具规划 | 原生结构化调用、参数解析、observation 再规划与预算 | 复合任务正确执行，未知工具与循环被拦住 |
| 检索与记忆 | 多路检索、重排、token 预算、增量摘要与重建向量 | 同题集召回与忠实度改善，超长对话成本受控 |
| 任务协调 | 执行者标识、fencing、动作幂等与全局配额 | 旧 worker 迟到写入被拒绝，多进程总并发受控 |

以上是设计建议。答题时不要把它们列成当前功能或已完成的个人成果。


[R01]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/agent/graph.py#L12
[R02]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/agent/runtime.py#L161
[R03]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/agent/state.py#L8
[R04]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/api/routes/conversations.py#L104
[R05]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/services/model_gateway.py#L25
[R06]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/services/plugin_registry.py#L55
[R07]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/mcp/registry.py#L8
[R08]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/services/tool_executor.py#L21
[R09]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/agent/response_policy.py#L23
[R10]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/services/rag.py#L97
[R11]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/core/mcp_security.py#L23
[R12]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/services/agent_task_runner.py#L79
[R13]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/services/task_scheduler.py#L26
[R14]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/services/task_schedules.py#L30
[R15]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/api/routes/tasks.py#L79
[R16]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/models/task.py#L66
[R17]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/main.py#L50
[R18]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/api/routes/auth.py#L31
[R19]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/api/routes/knowledge.py#L97
[R20]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/frontend/src/api.ts#L267
[R21]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/api/routes/memory.py#L17
[R22]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/core/config.py#L8
[R23]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/.github/workflows/quality.yml#L1
[R24]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/services/web_search.py#L23
[R25]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/evals/runner.py#L20
[R26]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/plugins/read_file/tool.py#L19
[R27]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/tests/test_agent_runtime.py#L556
[R28]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/tests/test_agent_task_runner.py#L126
[R29]: https://github.com/u2piaaa/AgentDemo/blob/cf58c0751e9ad6d674a126ed86c566dc25e41264/backend/app/mcp/server.py#L16
