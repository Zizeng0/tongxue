# 同学实验工作台

Windows 本地实验任务工作台。当前版本提供资料上传、PDF 与图片文字解析、需求填写、软件选择、按首句命名的会话草稿，以及可续聊的历史会话。HFSS 工作流可从指导文件提取当前任务的章节，生成参数化实验脚本并调用本机 HFSS；其他软件仍只提供实验规划。

## 本地运行

需要 Node.js 20.19+ 和 Python 3.10+。双击根目录 `index.html` 即可查看页面。该文件使用 `dist/` 中的静态构建结果；修改前端后运行 `npm install` 和 `npm run build` 更新它。

在 PyCharm 2026.2.2 中打开项目根目录，即可同时查看 `server/` 后端和 `src/` 前端。后端解释器选择 `.venv\Scripts\python.exe`；前端开发命令 `npm run dev` 从项目根目录执行。PyCharm 生成的 `.idea/` 是本机工作区设置，不提交到 Git。

上传与草稿保存需要启动本地服务：

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r server/requirements.txt
.\.venv\Scripts\python -m uvicorn server.app:app --reload --port 8000
```

另开一个终端：

```powershell
npm install
npm run dev
```

开发页面位于 `http://127.0.0.1:5173/dev.html`。Vite 将 `/api` 请求代理到本地 FastAPI 服务。任务草稿保存在 `data/tasks/`，该目录不会进入 Git。
若直接打开根目录 `index.html`，浏览器会从本地服务的 8000 端口读取和删除会话。修改后端代码后请重启该服务；使用上面的 `--reload` 启动方式可在修改时自动重载。

## MySQL 连接

将 `.env` 放在项目根目录（与 `README.md` 同级）；可按 `.env` 填写 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD` 和 `MYSQL_DATABASE`。后端每次建立新连接时读取该文件，已设置的进程环境变量优先。默认主机为 `127.0.0.1`、端口为 `3306`、用户为 `root`。修改 ORM 连接配置后重启服务，以重建缓存的 Engine。`.env` 已被 Git 忽略。

安装依赖后，可用以下命令确认认证和数据库选择成功：

```powershell
.\.venv\Scripts\python -m pip install -r server/requirements.txt
.\.venv\Scripts\python -c "from server.database import connect_mysql; connection = connect_mysql(); print(connection.get_host_info()); connection.close()"
```

原生 PyMySQL 使用连接和游标执行参数化 SQL。写入后调用 `commit()`；异常时调用 `rollback()`，最后关闭连接。下例的 `experiments` 表及字段需要与实际数据库一致：

```python
from server.database import connect_mysql

connection = connect_mysql()
try:
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, name FROM experiments WHERE id = %s", (1,))
        print(cursor.fetchone())
        cursor.execute("UPDATE experiments SET name = %s WHERE id = %s", ("新名称", 1))
    connection.commit()
except Exception:
    connection.rollback()
    raise
finally:
    connection.close()
```

PyMySQL 不提供 `Base`、`@Mapper` 注解或 ORM 会话；`@Mapper` 是 Java MyBatis 的写法。本项目使用 SQLAlchemy 2 的 `DeclarativeBase` 创建 `Base`，模型继承它并用 `Mapped`、`mapped_column` 定义字段映射：

```python
from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, mapped_column
from server.database import Base, get_session

class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))

with get_session() as session:
    experiments = session.scalars(select(Experiment)).all()
    session.add(Experiment(name="新实验"))
    session.commit()
```

`get_session()` 返回一个 SQLAlchemy `Session`；`with` 会负责关闭它，写入仍需显式 `commit()`。连接与会话函数位于 `server/database.py`。现有会话草稿仍保存在 `data/tasks/`，不会自动迁移到 MySQL。

## 登录与用户管理

前端首次打开时显示登录页，也可通过“立即注册”进入注册页。注册成功后服务返回 JWT，浏览器把它保存在本地存储，并自动为工作台请求添加 `Authorization: Bearer <token>`。令牌无效或过期时会清除并跳转到登录页。后端 JWT 校验位于 `server/features_services/jwt_validation.py`，密钥使用 `.env` 中的 `JWT_SECRET`。

工作台接口、历史任务和软件选择都要求登录。管理员账号（`identity=1`）才会在左侧看到“用户列表”，可以编辑用户名、头像、密码、状态和身份，也可以在确认弹窗后删除其他用户。用户头像菜单提供“个人信息”和“退出登录”。用户密码使用 PBKDF2-SHA256 加盐摘要保存，接口不会返回密码字段。

## 会话与模型

`GET /api/tasks` 读取历史会话，`POST /api/tasks` 创建会话或追加消息，`DELETE /api/tasks/{conversation_id}` 删除会话及其上传文件。前端的请求与返回类型分别位于 `src/contracts/requests/` 和 `src/contracts/responses/`；后端对应格式位于 `server/contracts/requests/` 和 `server/contracts/responses/`。持久化逻辑集中在 `server/conversations.py`。

默认只保存草稿。需要模型回复时，在启动后端的同一终端设置 `CHAT_MODEL`，例如 `$env:CHAT_MODEL = 'openai:gpt-4.1'` 并配置对应提供商的 API 密钥；也可启动 Ollama 并继续使用 `$env:OLLAMA_MODEL = '已安装的模型名'`。后端会按请求中的 `software_id` 选择 `server/agent/` 下的 Modelsim、Quartus、Packet Tracer 或 Ansys HFSS 工作流，将与当前要求相关的 PDF 页级片段及当前图片附件发给模型。所选模型需要具备处理图片的能力才能读取图片附件。HFSS 工具在用户明确要求执行时才调用本机 HFSS；求解失败会返回日志状态，不会把软件示例结果表述为实测。

每个会话只能在首次发送时上传一份指导文件，之后指导文件固定在历史会话中，后续消息仍可添加任务附件。指导文件 PDF 上限为 120 MB；PDF 附件上限为 100 MB；图片附件上限为 25 MB。PDF 上传后，后端会在消息目录的 `guide_analysis/` 或 `attachment-N_analysis/` 中保存 `analysis.json`、`segments.json`、页面图和内嵌图片；图片附件会保存 `attachment-N_analysis.json`。解析器使用 PyMuPDF 和 RapidOCR，依赖由 `server/requirements.txt` 安装。也可以单独运行 `./.venv/Scripts/python -m server.General_Tools.pdf_analysis <PDF路径> <结果目录>` 提取 PDF 页面证据。页级结果记录文字层、OCR 文字、置信度、数字差异、原图分辨率及 `needs_review`；逻辑片段记录页码和章节。Agent 依据当前要求从逻辑片段中选择相关内容，低分辨率或识别冲突的内容仍需人工对照原图。

## HFSS 本机工作流

HFSS 工具位于 `server/General_Tools/hfss_tools.py`，Agent 工作流位于 `server/agent/hfss.py`。工具优先使用 `HFSS_EXE` 或 `HFSS_HOME`，再查找 `D:\HFSS\HFSS15.0\Win64\hfss.exe` 和常见 AnsysEM 安装目录。Agent 可查询章节片段和操作参数格式，以 `project_path`、`output_dir`、`steps`、`solve` 等字段调用通用工具。`steps` 支持长方体、圆柱、球、圆、矩形、移动、旋转、缩放、沿线复制、布尔运算、材料和端口；波端口可按物体名及面上坐标在 HFSS 运行时解析面 ID。其余允许的 HFSS 脚本方法可传 `module`、`method` 和 `args`。执行器逐项核对状态、工程文件和新截图，再依据求解日志判断结果是否可用。`data/references/HFSSdrawpy` 是本地拉取的 HFSSdrawpy 源码参考；HFSS 15 的工程批处理使用其自带 VBScript API，不要求安装 HFSSdrawpy。

按指导书完成的 2.2/2.3 工程及步骤截图位于 `output/hfss/tee_22_23/`；4.1 至 4.8 的工程及步骤截图位于 `output/hfss/modeling_41_48/`；两份 Word 报告位于 `output/hfss/word_reports/`。2.2/2.3 建模和参数扫描已记录，但本机 HFSS 15 批处理求解报 `Error decoding model data or writing it to disk`，没有实测 S 参数和电场。复制的 HFSS 官方 Tee 样例在 `output/hfss/solver_probe/` 中也出现同一错误，因此现有证据只能将问题定位到本机求解运行链路，不能断定是实验几何体所致。4.7 原生 `ImprintProjection` 返回 `80070203`；`ProjectSheet` 已作为替代操作验证。4.4 的 Fit All 和显隐操作、4.5 的 Multi 设置与单面选择已有 HFSS 窗口截图，其余 GUI 选项仍待逐一核查。

## 验证

```powershell
npm test
npm run build
.\.venv\Scripts\python -m pytest server/tests
& .\tests\AGENTS.Tests.ps1
```

接口约定见 [产品设计文档](docs/product-design.md)。
