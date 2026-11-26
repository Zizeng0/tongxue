# 会话与头像存储

在项目根目录的 `.env` 中配置 MySQL 连接：`MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`。启动服务后，首次使用会自动创建实验会话的 `conversations` 表和解题会话的 `solution_conversations` 表；上传文件和解题生成图仍保存在 `data/tasks/`。旧版解题记录仍可从原表读取。

后端模块按职责放置：`server/agent/lab-report/` 包含实验工作台模型工作流和工具，`server/agent/solving-problems/` 包含解题流程、知识库检索和绘图工具；`server/data-operations/database/` 放数据库连接与会话表模型，`server/data-operations/session/` 放本地会话和迁移代码。今后新增同类脚本放入对应目录。原路径保留兼容入口；`server/General_Tools/` 中的通用 Python 工具保持原位。

模型默认使用 `deepseek-v4-pro`，直连 `https://api.deepseek.com`。在项目根目录 `.env` 或后端进程环境中设置 `DEEPSEEK_API_KEY`；`API_KEY` 只供显式选择 OpenAI 兼容模型时使用。`DEEPSEEK_BASE_URL` 可覆盖 DeepSeek 地址，`CHAT_MODEL` 或 `OLLAMA_MODEL` 可覆盖默认模型。若显式设置 `CHAT_MODEL=openai:gpt-6-sol`，旧的 `OPENAI_BASE_URL` 和中转站适配仍可使用。每个实验任务会创建独立模型实例，任务结束后释放；未配置默认模型密钥时，实验消息会作为草稿保存，解题请求会返回模型未配置。每次模型请求最多等待 90 秒，超时后重试一次。后端终端会输出请求校验、文件保存与解析、模型初始化、回复生成和保存等阶段日志；日志不包含 API Key 和消息正文。

实验任务由 `server/agent_pool.py` 的线程池调度。同一会话的执行不会重叠，不同会话最多同时运行 `AGENT_MAX_WORKERS` 个任务；`AGENT_MODEL_NAME` 和 `AGENT_MODEL_FACTORY` 可在该脚本中指定模型配置。日志中的 `agent_job` 是本进程单调递增的任务序号，`thread_id` 是操作系统原生线程号，`module` 和 `conversation` 标明功能及会话；线程号可能复用，排查一次执行时以 `agent_job` 关联。HFSS 和 PyCharm 工作流上限为 180 步，首次触顶后根据已保存文件自动续跑一次。续跑会重新构造模型上下文并读取已有成果，避免无限累积工作流消息；第二次触顶仍提示在原会话继续。

解题会话消息保存 `processing_seconds` 总耗时及 `stage_seconds` 阶段耗时，包括图片 OCR、视觉识别、模型初始化、Wiki 检索、教材检索、消息保存和答案生成。新增计时只适用于此后生成的消息，旧消息无法追溯阶段明细。

## 室友

「设置 → 外观设置 → 字体大小」支持下拉预设和直接输入，默认正文字号为 14.3 px。「设置 → 输出设置」可按账号保存「详细、丰富」「正常」或「简略」。该设置进入实验工作台、解题和室友的生成提示，本轮明确要求优先。直接展示存量题解或导出已有正文时保留原内容。

在侧栏打开「室友」，输入要完成的任务并按需添加附件。室友识别意图后调用解题、PPT 或 Word 工具。题解直接显示在对话中。文件统一在「我的项目」下载。输入「导出 Word」可导出最近一次题解，也可添加 UTF-8 文本或 Markdown 文件并要求导出 Word。需要修改时，在对话框主动输入修改要求。

可直接提供主题制作 PPT，也可以上传一个 `.pptx` 作为模板。模板应在母版中提供带标题和正文占位符的版式。生成时使用模板的母版和版式来编排新页面，示例幻灯片不会进入结果。插图优先使用模板的图片占位符，也可以与正文并排。未上传模板时，使用内置宽屏主题，支持列表、双栏、流程、重点和图文版式。总页数支持 2–20 页，含封面。用户指定明确页数时，服务会校验生成结果。可上传 PDF、TXT、Markdown 作为内容素材，最多四张图片和四份文件。图片、PDF 和模板每份最多 25 MB，文本每份最多 256 KB。PPT 工具放在 `server/General_Tools/ppt-operations`，通过 `python-pptx` 生成可编辑幻灯片。

PDF 优先读取文字层。默认每份最多读取前 20 页，PPT 可按标题定位章节，PPT 和解题也可以指定页码。扫描页通过视觉 API 识别，每轮最多三页。若素材超出读取范围，会在回复中标明。

PPT 模板按用途分为教学、专业知识讲解、演讲、竞赛，分别采用学习目标与练习、原理与证据、单页重点与讲稿、方案与验证指标的编排。模板和配色主题可以独立指定，例如「做 6 页竞赛答辩 PPT，使用研究浅蓝主题」。内置版式可生成 PowerPoint 原生对照表和可编辑指标，上传模板时沿用其占位版式。可参考 [SlidesCarnival 教学模板](https://www.slidescarnival.com/tag/education)、[研究模板](https://www.slidescarnival.com/tag/research)、[Duarte 演讲资料](https://www.duarte.com/resources/)和 [YC 路演指南](https://www.ycombinator.com/library/2u-how-to-build-your-seed-round-pitch-deck)。

室友读取当前账号的长期偏好、同一会话的最近四轮、更早的摘录和附件记录。可以直接问「上一轮输出了什么」或「之前上传了哪些文件」。续接任务时，写明历史文件名可以复用原文件。修改之前生成的 PPT 时，室友会读取所选版本的大纲、模板和配图。

PPT 制作借鉴 [PPT Master](https://github.com/hugohe3/ppt-master) 的内容策略、逐页编排和验收流程。室友使用账号配置的文本 API 规划每页的结论、依据和图像用途，随后编排并校验版式、字段、图片引用和页数，再用 python-pptx 导出。内置模板包括教学、专业知识讲解、演讲、竞赛、研究汇报和实训工作坊。

PPT 的「简略」「正常」「详细、丰富」分别以 5–7、7–10、9–14 页作为未指定页数时的编排参考。模型依据素材量确定页数。本轮指定的页数和文稿要求优先。详细档还会分批扩充逐页讲稿。正文可使用分层小标题、对照、结构图和带图注的双图版式。

PPT 优先从上传的 PDF 提取带图号的插图。能按标题定位章节时，优先从该章节开始读取，并在图下标明 PDF 来源页。联网配图按主题检索 Openverse，结果不足时补查 Wikimedia Commons，素材来源与许可证记录在对应页。要求图文结合时，回复会报告实际嵌入的图片数量，或说明缺图。可以在请求中写「只用我提供的素材」或「不要联网」。

「详细、丰富」档会主动为封面和正文检索公开配图，并在回复中报告实际采用的联网图片数量。内置封面有图时使用左右分栏，保留图片比例与出处。无图时使用主题色块和可编辑图形。上传模板沿用模板的封面布局和图片占位符，正文也可使用联网配图。

图库查询和图片下载使用后端进程可见的代理设置。Windows 下可使用运行后端的账号已启用的系统代理，也可在启动后端的环境里设置 `HTTPS_PROXY`。查询超时会重试一次，仍失败时，后端日志会记录失败的图库域名及 HTTP 状态或异常类型。编排会剔除未下载或不存在的图片编号，双图页只剩一张图时会采用单图版式。

室友优先按规则处理「解题」「修改格式」「导出 Word」等明确指令。其余可独立判断的请求使用云端向量 API，找出余弦相似度最高的五条示例，按路由计算平均分。最高分严格超过阈值且对应已注册能力时，室友调用该能力。

云端向量默认使用阿里云百炼 `text-embedding-v4`，读取项目根目录 `.env` 或后端环境中的 `DASHSCOPE_API_KEY`。默认使用北京兼容地址 `https://dashscope.aliyuncs.com/compatible-mode/v1`。若视觉 API 已指定百炼区域，会沿用相同地址。可以通过 `ROOMMATE_EMBEDDING_BASE_URL`、`ROOMMATE_EMBEDDING_MODEL` 和 `ROOMMATE_EMBEDDING_API_KEY` 指定其他兼容服务，地址填 HTTPS API 前缀，例如以 `/v1` 结尾。请求仅提交本轮待匹配文本或内置示例。室友在进程内缓存云端示例向量，并用同一个模型处理本轮请求。阈值可通过后端环境变量 `ROOMMATE_INTENT_THRESHOLD` 调整，默认 `0.55`。

需要结合对话、指令有冲突或向量服务不可用时，室友使用「设置 → 模型设置」里配置的文本 API 决策。分数不足、得分并列、材料缺失或超出当前能力时，室友会在原对话询问具体目标或所缺材料。新增 Agent 时可在 `server/agent/roommate_routing.py` 注册能力描述、规则和示例，并在 `server/agent/roommate.py` 注册处理函数。

修改论文格式时，上传 `.docx` 原稿并写明要求，例如「修改论文格式，正文宋体小四、1.5 倍行距，左页边距 3 厘米」。也可一起上传带文字层的 PDF 规范、Word 规范或 Word 样文。多个 Word 文件的用途不明确时，室友会询问原稿或样文的文件名。在原对话补充材料时可以沿用之前的原稿。明确要求「按通用论文格式排版」可使用通用样式，学校规范和明确要求优先。通用样式参考 [Chinese Thesis Workbench](https://github.com/ZyhSechub/chinese-thesis-workbench-skill)。

论文排版可调整纸张、页边距、字体、字号和段落格式。系统依据 Word 样式与常见标题、题注特征判断段落类型，保留原有正文、表格、图片、公式和页眉页脚，另存可编辑的「原稿名-排版版.docx」到「我的项目」。PDF 可作为书面规范，修改原稿需要 `.docx`。Word 样文提供可识别的版式，复杂封面、页眉页脚、页码及目录生成可在 Word 中处理。

在室友输入「写一篇关于校园雨水回收的论文」或「列一份关于校园雨水回收的论文提纲」，可在「我的项目」下载可编辑 Word。支持随请求上传 DOCX、带文字层的 PDF、TXT 和 Markdown 作为资料。未给出研究方向时会追问。输出设置为「详细、丰富」时，系统先确定研究问题和范围，检索 Crossref 书目与可用摘要，组织逐节框架，生成后再审阅引文与论证。正常和简略档采用较短流程，简略档有上传资料时优先使用这些资料。输入「不要联网」可跳过公开文献检索，撰写仍由所配置的文本 API 完成。Word 会标明资料范围和可追溯来源。可用文献证据取决于 Crossref 提供的元数据和摘要以及用户提供的资料。

题库走题号、图片模板或哈希匹配，以及关键词查询。关键词结果标为相似题。向量匹配用于意图识别，题图无法匹配时通过配置的视觉 API 读取，文本 API 负责生成答案。

旧版 `task.json` 不包含用户归属，不会自动显示给任何账号。确认归属账号的数据库 ID 后，在项目根目录运行：

```powershell
.venv\Scripts\python -m server.migrate_conversations --owner-id 1
```

导入可重复运行，不会重复写入。头像在个人信息弹窗中上传，文件保存在 `data/avatars/`，`users.image` 保存 `/api/users/avatars/...` 路径。

## 指导文件按需解析

上传 PDF 时只保存原文件。模型开始处理任务后，会先看到页数和 PDF 自带书签；长目录可调用 `list_pdf_bookmarks` 分批查看，再通过 `read_pdf_pages(document_id, start_page, end_page)` 选择相关页；单次最多读取 5 页。PyMuPDF 优先读取所选页的文字层，仅对没有文字层的所选页运行 RapidOCR，并缓存 OCR 文字。没有书签的扫描件需要模型逐批浏览页面，定位章节可能需要更多次工具调用。原有 `analyze_pdf` 完整分析命令仍可手动使用，但发送任务时不再运行。

实验报告模板支持 DOCX 和旧版 DOC。DOC 会保留原件并转换为 DOCX，供报告工具读取；Windows 服务主机需要安装 Microsoft Word，其他系统需要安装 LibreOffice。任务图片旁的“其他文件”可上传实验素材：图片、PDF、TXT、CSV、TSV、JSON、PY、XLSX、XLS、NPY、DOC、DOCX；图片单文件上限 25 MB，其余文件 100 MB。图片进入模型消息，PDF 可按需选页，文本和 DOCX 可调用 `read_experiment_file`；PyCharm 脚本可使用模型收到的文件路径读取原始素材。

报告工具要求每个章节有正文或图片，不能只写标题。上传的模板含 `{{content}}` 时，在占位符处插入章节；没有该占位符时，按与模板完全相同的小节标题填入对应位置，其余章节追加在文末。较长模板若没有任何同名小节，会返回错误，要求模型按模板标题重试。若当前会话没有上传报告模板，报告使用默认版式，模型回复会说明这一点。模型未生成报告文件时，会话保存明确的失败状态。

参考项目为 GitHub 上的 [PyMuPDF-Utilities](https://github.com/pymupdf/PyMuPDF-Utilities)（AGPL-3.0）；已将其中的 OCR 示例、README 和许可证按 `SOURCE.txt` 记录的版本下载到本地 Git 忽略目录 `data/external/PyMuPDF-Utilities-OCR/`。运行代码使用项目已安装的 PyMuPDF 和 RapidOCR，没有复制该示例项目的源码到服务模块。

## HFSS 螺旋体工具

创建螺旋体或平面螺旋体时，先用 `create_circle` 建立截面，再在 `steps` 中使用 `create_helix` 或 `create_spiral`。两个动作需要 `object`（已创建圆的名称）、三维 `center`、三维 `start_direction` 和正整数 `turns`；`create_helix` 另需 `pitch`，`create_spiral` 另需 `radius_increment`。`create_helix` 也可设置可选的 `radius_increment`。`right_hand` 默认为 `true`。`describe_hfss_experiment_spec(topic="geometry")` 会返回完整示例。`prepare_parameterized_hfss_experiment` 会在启动 HFSS 前检查参数。截图结果中的 `valid_evidence` 只有在对应操作成功且截图为本次新生成时才为 `true`；请勿将失败步骤后的截图当作完成证据。
## 会话记忆

实验工作台和解题都将用户原文及助手每个已保存的答复版本追加到 MySQL 的 `conversation_ledger`，并在各自 `data/tasks/<会话>/ledger/` 下镜像为独立 JSON。原有两张会话表继续提供历史和文件接口，归档会话会从工作台及项目列表隐藏，原始记录和文件保留。

会话列表的标题和创建时间写入 `conversation_memory_views`。`conversation_user_profiles` 按账号记录用户明确表达的长期偏好，例如“以后优先用表格比较”。模型上下文保留最近四轮，将更早的同会话内容摘录成短摘要。当前请求优先于档案和历史。

Redis 运行后，在后端环境或 `.env` 设置 `REDIS_URL=redis://127.0.0.1:6379/0`，安装 `server/requirements.txt` 中的依赖。成功登录会更新 `tongxue:profile:<用户ID>` 哈希，MySQL 保留档案的持久记录。

已有会话和缺失的 JSON 镜像可以重复补录：

```powershell
.\.venv\Scripts\python.exe -m server.backfill_conversation_memory
```

## MCP 工具与实验任务

管理员登录后，可从左侧「MCP 服务」直接检索文献或读取自己的项目 PDF。页面自动使用当前登录令牌。读取 PDF 可选连续 50 页，结果每屏显示 5 页，并按当前屏加载整页预览。

安装后端依赖并启动 FastAPI 后，支持自定义请求头的 MCP 客户端可用 Streamable HTTP 连接 `http://127.0.0.1:8000/mcp/`。在客户端设置 `Authorization: Bearer <登录令牌>`，令牌使用工作台登录返回的 JWT。

- `search_papers(query, limit)` 通过 Crossref 按关键词检索公开书目、DOI 和可用摘要，每次最多五条。
- `read_pdf_pages(conversation_id, file_id, start_page, end_page)` 读取当前账号项目附件中的 PDF 文字层，每次最多 50 页，页码从 1 开始。会话与文件标识可从 `GET /api/projects/{conversation_id}` 获取，`file_id` 使用该接口返回的文件 `path`。扫描页返回 `no_text_layer`。
- `preview_pdf_page(conversation_id, file_id, page_number)` 返回一页 PDF 的 JPEG 预览。包括扫描页、嵌入图片和矢量图，预览在内存中生成。
- `render_presentation(deck)` 将结构化编排按内置版式生成可编辑 PPTX，支持 2–20 页（含封面）。例如 `{"deck":{"title":"实验","slides":[{"title":"结果","points":["压力为 12 kPa"]}]}}`。
- `render_thesis(manuscript, sources, disclosure)` 将已有正文生成可编辑 DOCX。正文段落使用 `sources` 中提供的编号关联书目，未提供的引用会报错。
- `plot_discrete_sequence(samples, start_index, title)` 返回解题工具绘制的 PNG 茎状图，支持 1–256 个有限实数样本。

管理员可在「MCP 服务 → 生成与绘图工具」直接运行预填示例或编辑 JSON 参数。三个工具复用室友的文件生成器或解题绘图器，在内存中生成文件，由调用方下载。自然语言选题、撰写、编排和联网配图仍由对应的室友工作流处理。

实验工作台工具支持 `experiment_capabilities`、`plan_experiment`、`describe_hfss_spec`、`extract_hfss_parameters`、`list_project_pdf_bookmarks`、`read_experiment_input` 和 `inspect_experiment_template`。项目资料读取按登录账号校验归属。

管理员在实验工作台建好会话并上传 PDF 指导书后，可从「MCP 服务 → 提交实验任务」发起后台工作流。外部客户端调用 `submit_experiment_task` 时，传入 `conversation_id`、`software_id`、`requirements`、`deliverables`、UUID `request_id` 和 `confirm_execution: true`。同一会话的同一请求编号与参数可安全重试。通过 `get_experiment_task(conversation_id, request_id)` 查询排队、运行、草稿、完成、失败或中断状态，并在「我的项目」检索产物。

HFSS 提交前检查本机可执行文件，运行与求解通过已有的项目内工具完成。Modelsim、Quartus 和 Packet Tracer 工作流提供规划与报告。MCP 提交的 PyCharm 工作流生成和检查源码，实际脚本执行与桌面采集使用实验工作台的本地入口。工作流完成表示已取得流程回复，求解结果仍以 HFSS 日志和项目产物为准。

室友在意图路由完成后由各工作流按固定用途调用共享工具。论文在文献检索阶段复用 Crossref，沿用详细档最多 3 个查询和 9 条结果、其他档最多 1 个查询和 5 条结果的设置。「不要联网」仍会跳过文献检索。PPT 和解题在准备素材时读取本轮或对话已经选定的 PDF，可以指定「参考讲义.pdf 第 23–24 页」。多份 PDF 时应写明文件名。室友沿用单轮文字预算、每份 PDF 最多 20 页和单轮最多 3 个扫描页的视觉识别限制。PPT 无指定页码时可继续按标题定位章节。
