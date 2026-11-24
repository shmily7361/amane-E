# AGENTS.md

本文件为各种 Coding Agent 提供引导.
**绝对禁止此文件长度超过 200 行.**

## 用语规范

**最高优先级.** 凡需语言表达的输出 (注释、开发文档、提交说明、Pull Request、与维护者的对话) 必须遵守 [`docs/dev/writing.md`](docs/dev/writing.md) (遣词造句) 与 [`docs/dev/terms.md`](docs/dev/terms.md) (项目用词). 与本文其它条款冲突时以这两份为准.
两份都是按需查阅: 引入新概念、写用户文案或给概念改名时查 `terms.md`, 不要求通读.

## 项目定位

**Amane** — 影片元数据服务. 核心是以番号为键的元数据条目: 多源刮削、本地留存、Web UI 管理与智能体. 媒体库扫描、整理与播放是可选外围功能, 核心流程不得以本地文件为前提. 与 Emby/Jellyfin 互补而非替代.

技术栈: FastAPI · React + Mantine · SQLite (SQLModel + Alembic) · Docker · PyInstaller + 原生桌面 APP (swift / .NET AOT) · Android 壳 (Kotlin WebView)

## 目录结构与核心架构约定

| 路径 | 职责与边界 |
|------|-----------|
| `src/amane/api/` | FastAPI 路由、请求/响应模型、依赖注入、`support/`。只做 HTTP 适配，不承载业务逻辑。 |
| `src/amane/handlers/` | 任务 payload 与 handler。SCRAPE / ORGANIZE / REFRESH / CLEANUP 等领域逻辑在此；不抛 `HTTPException`。 |
| `src/amane/agent/` | 工具定义、能力注册、会话与服务。依赖通过 `AgentDeps` 注入，不依赖 API 层。 |
| `src/amane/db/` | `models.py` / `repos/` / `migrations/`。迁移只由 Alembic 生成；写锁、批量提交等约束在 repo 层。 |
| `src/amane/organize/` | 路径模板、视频与字幕落盘、附属资源搬迁。`parse_file_info` 只做文件名解析，不访问 DB。 |
| `src/amane/library/` | 媒体库扫描、清理清单、删除与剪枝。清理判定与执行复验必须同口径。 |
| `src/amane/media/` `parsing/` `crawlers/` | 资源物化、番号/文件信息解析、站点爬虫。爬虫不改数据库。 |
| `src/amane/scheduler/` `events.py` `observability/` | worker、cron、watcher、事件总线与日志转发。跨模块通知走 EventBus 或结构化日志。 |
| `web/src/` | React 前端。`client/` 由 OpenAPI 生成，禁止手改；路由在 `routes/`，组件在 `components/`。 |
| `tests/` | 测试镜像源码结构。共享 fixture 在 `conftest.py`，迁移模板在 `schema_template.py`。 |

核心约定: **Metadata 是一等公民**，不以本地文件存在为前提；`MediaFile` 是磁盘文件索引。任务通过 payload/handler 执行，链内后继在父任务完成事务中创建。API 与 Agent 都通过 handlers 调用领域逻辑，不得反向依赖 `api.*`。

本分支额外约定: 保留 `handlers/enqueue.py` 的领域层入队与 `pending_writebacks` 失败补偿、`organize/sidecars.py` 的旧资源搬迁与 `.bif` 随行、`db/models.py::NameMapping` 的文件名→番号映射。重构时不得回退这些机制。

## Issue 与 Pull Request

外部贡献必须遵守 [`AI_POLICY.md`](AI_POLICY.md). PR 须经人工 review, 并按该政策披露 AI 使用程度.

## 远程操作

**所有影响远程仓库的变更必须经用户明确批准后才能执行.** 例如 push、合并 PR、回复 Issue、发表评论.

## 代码知识库

位置: `docs/dev/` — 导航见 [`docs/dev/index.md`](docs/dev/index.md).
**优先读文档**, 根据文档按需读源码.

### 文档的用途

`docs/dev/` 只服务 Coding Agent, 不服务人类读者或终端用户. 它的唯一作用是**索引**: 用最少的阅读量把 Agent 送到一两个具体文件, 送到之后仍要读那些文件.
因此文档不求自足, 不承载实现细节; 一份文档的价值等于它替 Agent 省下的源码阅读量.

### 内容归属

一条信息写在哪里, 看它的影响半径:

| 信息 | 归属 |
|------|------|
| 源码本身已经说明的事实 (字段、签名、枚举、默认值、清单、正则、目录树、命令用法) | **都不写**, 让代码自注释 |
| 单个文件内部的实现原因与约束 | **该文件的注释 / docstring** |
| 跨模块的边界、顺序、契约、取舍 (模块间联动、前后端联动、启动顺序、持久化格式、仍生效的禁止事项) | **`docs/dev/`** |

判据: 删掉这条信息, Agent 会不会因此多读文件或读错文件? 不会就删.

### 维护

修改代码后必须同步更新对应文档. 遵循以下规则:

1. **先精简再加**: 每次改动都顺手压缩所在段落, 新信息优先并入既有句子. 文档长度只允许持平或变短, 除非确实新增了跨模块契约.
2. **分层组织**: 单个文档简短、主题集中; 形成树结构逐级索引, 根为 `index.md`.
3. **不重复源码**: 只写无法从源码直接读出的信息 (边界、顺序、契约、取舍、踩坑). 禁止罗列字段、复制签名、抄注释、粘贴可机械读出的源码片段.
4. **只写当前状态**: 文档只描述现在是什么、为什么, 以及仍生效的禁止事项.
   **禁止**背景铺陈与迁移动机 (「从 A 改成 B」「以前是…」). 禁止事项的写法见 [writing.md](docs/dev/writing.md).
5. **不举例**: 禁止用示例、场景枚举或对照说明代替规则本身.
6. **精准引用**: 指向文件用仓库相对路径加符号名, 如 `src/amane/handlers/scrape.py::ScrapeHandler.handle`. 不写行号 — 行号随改动失效.
7. **正交性**: 同一内容只出现在一个文档; 交叉引用用相对链接.
8. **不新增文档**: 新主题优先进既有文档; 确需新建时同步登记到 `index.md`.
9. **AGENTS.md 保持简洁**: 只留最重要信息, 细节在 `docs/dev/`.

## 开发命令

前置条件: uv, pnpm, [just](https://github.com/casey/just)

Python 用 uv, 前端用 pnpm; **对外任务入口是根目录 Justfile** (`just --list`). 不要用根 `package.json` 编排.

```bash
just setup      # 同步依赖 + 拉取测试 fixture + 安装 prek hooks
just sync       # 仅同步 Python + web 依赖

just dev        # 并行启动 API (:8000) + Vite web

just build      # 导出 OpenAPI + 构建前端
just generate   # 导出 openapi + 生成 TS client (alias: just api)
just icons      # 从 assets/logo.svg 生成 favicon / ico / icns

just test       # pytest
just test-cov   # pytest + 覆盖率报告
just check      # lint + format + typecheck + web check + test
just fix        # ruff / oxfmt / oxlint 自动修复
just all        # generate → fix → check → build

just bump patch|minor|major  # 发版: 升版本 + generate + 提交 + tag (不 push)
just bump-dry patch          # 预览下一版本
```

等价的具体命令 (排障或 just 不可用时直接使用):

```bash
# 后端
uv run pytest -q
uv run pytest tests/<模块>/test_<文件>.py -q
uv run ruff check .
uv run ruff format --check .
uv run ruff check --fix . && uv run ruff format .
uv run ty check
uv run alembic heads
uv run alembic upgrade head

# 前端 (在 web/)
cd web
pnpm gen-client          # 按 web/openapi.json 生成 src/client
pnpm check               # oxlint + oxfmt --check + tsc -b --noEmit + i18n:ci
pnpm build               # tsc -b && vite build

# 全量
just check               # lint + format + typecheck + web check + test
just build               # 导出 OpenAPI + 构建前端
```

formatter/linter 不通过时优先使用 `just fix` 自动修复, 不要手动改
发版前更新 `CHANGELOG.md`, 只用 `just bump`, 不要手改版本或手打 tag; Android APP 的版本与 tag 独立, 见 [android.md](docs/dev/android.md)

## 开发规范

### 通用

- 及时提交. 开发过程保持细粒度提交, 每个逻辑变更独立提交, 避免积压为单个巨大提交
- 同步更新知识库. 修改架构、API 或工作流时必须同步更新或增减文档
- 进度同步. 当需求来自 roadmap/todo 注释等, 在完成后移除相关内容

### Python

- **类型安全第一**. 所有函数/类/方法必须具有完备的类型标注, 使用 Pydantic model 而不是 dict 进行数据传递
- **禁止内部导入**. 所有导入一律放文件顶部 (`if TYPE_CHECKING:` 除外), 如果出现循环导入, 反思结构设计
- **禁止反射**. 禁止使用 `hasattr/getattr` (动态字段访问除外), 类型检查报错就修类型标注, 不要使用 `getattr` 绕过
- **禁止全局变量**. 使用 FastAPI 依赖注入, 通过构造函数或参数传递
- **数据库迁移必须用工具**. 必须通过 `uv run alembic revision [--autogenerate] -m "描述"` 创建, **绝对禁止手写 revision ID** 手动创建迁移文件

### TypeScript

- **绝对类型安全**. 禁止使用 as 类型断言 (必要情况需说明), **绝对禁止**使用 any (仅必要时使用 unknown)
- **静态优先**. 能在类型检查阶段完成的不要依赖运行时检查. 善用类型计算解决问题

### 测试

- **表测试优先**. 能使用表测试就不要创建多个测试函数/类等
- **复用fixture**. 创建 fixture 前先检查是否已有
- **禁止玩具测试**. 禁止为提高覆盖率而创建无意义的测试, 必须测试实际逻辑. 详见 `docs/dev/testing.md`
- **必须包含边界/非法用例**. 不能只包含合法输入用例, 必须充分测试非法输入下的健壮性, 以及报错异常信息的明确性

## 提交与 PR 前必查

按顺序确认，缺项不得提交或开 PR:

1. **分支与范围**: `git status --short --branch` 确认在目标分支、无意外文件；`git diff --check` 无空白错误。每个提交只含一个逻辑变更。
2. **测试**: 改动模块的目标测试通过；全量 `uv run pytest -q` 的失败集合与当前环境基线一致，不得新增 error。当前基线失败均为 Windows symlink 权限、camoufox 缺失与 image token 环境问题。
3. **后端质量**: `uv run ruff check .`、`uv run ruff format --check .`、`uv run ty check`。ty 只允许既有的可选浏览器依赖 `unresolved-import`。
4. **前端质量**: 在 `web/` 跑 `pnpm check` 与 `pnpm build`；有 lint warning 须确认不是本次引入。
5. **生成物**: API schema 变更必须 `just generate` / `pnpm gen-client`，提交 `web/openapi.json` 与 `web/src/client/` 的同步改动；不得手改生成文件。
6. **数据库**: 新增/修改表结构必须通过 Alembic revision；检查 `uv run alembic heads` 单 head，迁移含 upgrade/downgrade 测试。本分支 head 应能串到 `2f7b9a4c1d3e`。
7. **文档**: 行为、架构或命令变化同步更新 `docs/dev/` / `docs/user/` 与本文档；措辞遵守 `docs/dev/writing.md` 和 `docs/dev/terms.md`。
8. **回归风险**: 确认未回退本分支本地优化（增量写盘补偿、附属资源搬迁与 `.bif`、文件名→番号映射），未把临时日志、调试代码、密钥或生成缓存纳入提交。
9. **远程操作**: push、开 PR、合并、回复 Issue 等必须经用户明确批准；本分支默认只本地提交，不推送。
10. **PR 描述**: 说明改动动机、验证命令与结果、已知限制；按 `AI_POLICY.md` 披露 AI 使用程度，PR 必须经人工 review。
