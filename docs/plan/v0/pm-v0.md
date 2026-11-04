# 系统定位
面向多模态 AI 数据集的可扩展智能质量评测系统，是高质量数据集平台的一个横向基础服务，为数据集注册、数据加工、标注、版本发布、模型训练提供质量评测能力。系统应同时支持
- 结构化数据、文本、图像、音频、视频和多模态组合数据。
- 数据集级、样本级、字段级、模态级质量评测。
- 内置质量指标、自定义指标、领域规则、模型辅助评测。
- 手动评测、周期评测、数据变更触发评测、发布前质量门禁。
- 面向人工、平台服务和 Agent 的统一调用入口。
v0版本要求在结构化文本数据跑通，但保留可扩展能力。


# 架构图（Mermaid）

flowchart TB
    U["用户 / 数据管理员"]

    subgraph S1["接入与标准化层"]
        A1["数据集注册 Dataset Manifest"]
        A2["数据适配与标准化 Sample Record"]
        A3["数据画像 Dataset Profiler"]
    end

    subgraph S2["Agent 智能决策层"]
        B1["Quality Agent"]
        B2["评测策略推荐 Skill"]
        B3["质量诊断与修复建议 Skill"]
        B4["MCP Tools"]
    end

    subgraph S3["质量契约与计划层"]
        C1["Metric Registry 指标注册中心"]
        C2["Quality Contract 质量契约"]
        C3["Contract Validator 契约校验"]
        C4["Evaluation Planner 执行计划"]
    end

    subgraph S4["确定性评测引擎"]
        D1["Pipeline / Stage 编排"]
        D2["Metric Calculator 指标计算"]
        D3["Metric Aggregator 指标聚合"]
        D4["Rule Evaluator 规则判定"]
        D5["Issue Detector 问题检测"]
        D6["Executor 本地执行"]
    end

    subgraph S5["评测结果与反馈层"]
        E1["Metric Results 指标事实"]
        E2["Quality Issues 异常与证据"]
        E3["Quality Report 评测报告"]
        E4["Review & Feedback 人工反馈"]
        E5["Version Comparison 版本对比"]
    end

    subgraph S6["基础设施"]
        F1[("PostgreSQL 元数据 / 契约 / 结果")]
        F2[("MinIO 原始数据 / 报告 / 证据")]
        F3["模型服务 LLM / Embedding / 分类器"]
    end

    U --> A1
    A1 --> A2 --> A3
    A3 --> B1
    A3 --> C4

    B1 --> B2
    B1 --> B3
    B2 --> C2
    B1 <--> B4

    C1 --> C3
    C2 --> C3
    C3 --> C4
    C4 --> D1
    D6 -.->|执行 Pipeline| D1

    D1 --> D2
    D1 --> D5
    D2 --> D3
    D2 --> E1
    D3 --> E1
    D5 --> E2
    E1 --> D4
    C2 --> D4
    D4 --> E2

    E1 --> E3
    E2 --> E3
    E3 --> E4
    E3 --> E5
    E4 --> B3
    E5 --> B3

    B4 -->|执行 / 查询| C3
    B4 -->|执行 / 查询| D1
    B4 -->|查询| E3

    A1 -.-> F1
    C2 -.-> F1
    E1 -.-> F1
    E2 -.-> F1
    A2 -.-> F2
    E3 -.-> F2
    D2 -.-> F3
    D5 -.-> F3

# 业务模块
系统以 Dataset Version（数据集版本） 为评测对象，以 Quality Contract（质量契约） 为评测标准，通过逻辑计划与物理计划编译、指标计算、规则判定和问题检测，生成可追溯的质量报告，并支持人工反馈、数据修复、版本更新和复评，形成数据质量持续改进闭环。

核心业务流程：数据集注册/更新 → 样本标准化 → 数据画像 → 质量契约选择 → 逻辑计划生成 → 物理计划编译与优化 → 评测执行 → 指标结果 → 规则判定与问题检测 → 质量报告 → 人工反馈 → 数据修复 → 新版本注册 → 复评。


## 业务过程

1. 数据集注册与版本管理（Dataset Lifecycle Management）
- 输入： 用户上传的原始数据文件（JSONL、Parquet、多模态文件等），或已有数据源引用。
- 处理： 判断数据集是否已注册。首次上传创建 Dataset ID；已有数据集更新则沿用 Dataset ID，创建新的 Dataset Version。存储原始文件，完成格式、Schema、文件完整性校验，并记录来源、模态、存储位置及字段映射。
- 输出对象： Dataset、Dataset Version、Dataset Manifest。
- 约束： 已发布的数据集版本不可变；修复或更新数据应创建新版本。

2. 样本标准化（Sample Normalization）
- 输入： Dataset Manifest、原始数据文件、Schema Mapping。
- 处理： Dataset Adapter 根据数据格式及字段映射，将原始数据转换为统一 Sample Record，保留原始内容、语义角色、标注及来源血缘。
- 输出对象： Sample Record。
- 约束： 标准化不修改原始数据，转换失败应保留错误记录和来源定位。

3. 数据画像生成（Dataset Profiling）
- 输入： Dataset Version、Sample Records。
- 处理： Dataset Profiler 统计数据规模、Schema、模态、字段缺失、内容长度、语言分布等基础特征，并结合用户声明的任务用途生成画像。
- 输出对象： Dataset Profile。
- 约束： 画像属于数据特征描述，不直接进行质量合格判定；同一数据集版本可因画像算法升级生成新的画像结果。

4. 质量契约选择与配置（Quality Contract Selection）
- 输入： Dataset Profile、应用场景、Quality Contract Template、Metric Registry。
- 处理： 根据数据模态、行业、训练阶段和下游任务匹配质量契约模板。Agent 可以推荐指标、计算口径及规则配置，由用户确认后发布质量契约版本。
- 输出对象： Quality Contract、Contract Items。
- 约束： 契约声明评测指标、计算语义、质量阈值及必要的实现约束，原则上不强制绑定具体 Calculator Implementation。

5. 逻辑计划生成（Logical Planning）
- 输入： Dataset Version、Dataset Profile、Quality Contract、Metric Registry。
- 处理： Logical Planner 解析契约指标，检查数据适用性，展开指标与中间特征的依赖关系，构建与执行环境无关的逻辑评测 DAG。
- 输出对象： Logical Evaluation Plan。
- 约束： 逻辑计划描述需要计算什么及其依赖，不决定具体执行后端、并发度和资源配置。

6. 物理计划编译与优化（Physical Planning）
- 输入： Logical Evaluation Plan、Calculator Registry、运行环境及资源约束。
- 处理： Physical Planner 为逻辑节点选择满足契约约束的 Calculator Implementation，绑定算法、模型及参数；通过依赖去重、公共特征复用、缓存、批处理等方式优化 DAG，编排 Pipeline、Processing Stage 和执行依赖。
- 输出对象： Physical Evaluation Plan、Executable Pipeline。
- 约束： 优化不能改变指标计算语义；物理计划固化实际计算实现及配置，保证可追溯。

7. 评测执行与指标计算（Evaluation Execution）
- 输入： Physical Evaluation Plan、Sample Records。
- 处理： Executor 创建 Evaluation Run，按 DAG 依赖调度 Pipeline 中的算子；无依赖的任务可并发执行。Calculator 进行样本级、分组级或数据集级计算，Result Writer 统一校验并持久化结果及计算证据。
- 输出对象： Evaluation Run、Metric Result、计算证据及中间产物。
- 约束： 记录执行状态、失败原因、Calculator 版本、模型参数及输入指纹；支持失败重试和安全的计算结果复用。

8. 质量规则判定（Rule Evaluation）
- 输入： Metric Result、Quality Contract。
- 处理： Rule Evaluator 根据契约中的阈值、范围及组合规则，对指标结果执行确定性判定。
- 输出对象： Rule Evaluation。
- 约束： 区分 PASS、FAIL、UNKNOWN、SKIPPED；指标计算失败不直接等同于数据质量不合格。

9. 质量问题检测（Issue Detection）
- 输入： Metric Result、Rule Evaluation、Calculator/Detector Evidence。
- 处理： Issue Detector 识别规则超标、内容缺失、重复、标注冲突、低质量回答等异常，定位到数据集、分组、样本或样本对，完成问题归一化、去重及证据关联。
- 输出对象： Quality Issue。
- 约束： Quality Issue 不要求一定来自失败的规则；即使整体指标达标，也可能存在样本级质量问题。

10. 质量报告生成（Quality Reporting）
- 输入： Evaluation Run、Metric Result、Rule Evaluation、Quality Issue。
- 处理： Report Generator 汇总指标值、规则通过情况、问题分布、评测覆盖率及执行状态，生成结构化质量报告；可由 Agent 基于报告提供解释和改进建议。
- 输出对象： Quality Report。
- 约束： 结构化报告提供客观结果，Agent 不得擅自修改指标值及规则判定；评测执行状态与数据质量结论分别记录。

11. 人工反馈与修复（Human Feedback & Repair）
- 输入： Quality Report、Quality Issue、Metric Result 及相关证据。
- 处理： 用户审核质量问题，确认误报、纠正评分或提出修复建议，系统追加保存人工反馈。经授权后，修复流程根据反馈执行样本过滤、去重、标注纠正等操作。
- 输出对象： Feedback Record、Repair Proposal / Repair Action、修复后的候选数据集。
- 约束： 人工反馈与修复操作分离；不覆盖原始指标结果、问题记录或已发布的 Sample Record。

12. 数据集版本更新与复评（Version Update & Re-evaluation）
- 输入： 修复后的候选数据集、历史 Dataset Manifest、历史 Quality Contract。
- 处理： 重新进入数据集生命周期管理流程，发布 Dataset v2，生成新的 Manifest、Sample Records 和 Dataset Profile，并创建新的 Evaluation Plan 与 Evaluation Run。
- 输出对象： Dataset v2、Quality Report v2、版本质量对比结果。
- 约束： 优先使用相同的质量契约、指标口径和可比的计算配置进行复评；通过新旧版本质量指标与问题变化验证修复效果。下游模型性能提升需要额外的受控实验验证。

## 业务约束
- 版本不可变： Dataset Version、已发布 Quality Contract 及历史评测结果不可直接覆盖。
- 职责分离： Metric Result 记录计算事实；Rule Evaluation 记录达标判断；Quality Issue 记录具体异常；Quality Report 负责汇总呈现。
- 计划与执行分离： Logical Plan 描述计算需求，Physical Plan 绑定具体实现，Evaluation Run 记录一次实际执行。
- 证据可追溯： 每项质量结论应能够追溯到契约规则、指标结果、计算实现、数据集版本及必要的检测证据。
- 人工参与可审计： Agent 可以辅助推荐、解释和修复规划，但质量标准发布、重要修复操作及策略变更应受权限和审核机制约束。
- 闭环可验证： 数据修复必须通过新版本复评验证，评测策略优化则通过独立的离线评测验证，二者不能混为一谈。

## 模块设计
模块	业务模块	核心职责	关键输入	核心输出	借鉴来源	MVP 优先级
M1	数据集生命周期管理与样本标准化	统一管理数据集首次注册、版本更新、文件存储、Schema 映射与校验；生成不可变 Dataset Version 和 Dataset Manifest；通过 Adapter 将 JSONL/Parquet 等原始数据转换为统一 Sample Record，维护数据血缘	原始数据集、数据源配置、Schema Mapping	Dataset、Dataset Version、Dataset Manifest、Sample Records	TFDV、NeMo Curator	P0
M2	数据画像与指标注册管理	根据数据集版本生成规模、模态、Schema、缺失情况及内容分布等客观画像；统一注册 Metric Definition、Calculator Implementation、输入依赖、计算参数、输出 Schema、版本及适用范围，支持指标与实现解耦	Dataset Version、Sample Records、指标定义及计算插件	Dataset Profile、Metric Registry、Calculator Registry	Data-Juicer、TFDV	P0
M3	质量契约管理	根据数据画像、应用场景及用户配置选择或创建质量契约；通过 Contract Items 声明评测指标、计算口径、实现约束、质量阈值、检查范围及严重程度；完成契约合法性校验、版本发布与管理	Dataset Profile、Metric Registry、契约模板、用户配置、Agent 推荐	Quality Contract、Contract Items、Contract Validation Result	Soda Core、Great Expectations	P0
M4	评测计划编译与任务管理	将数据集版本与质量契约绑定；Logical Planner 解析指标依赖并生成逻辑 DAG；Plan Optimizer 实现依赖去重、公共计算复用与批处理优化；Physical Planner 绑定具体 Calculator、模型及执行参数，编排 Pipeline；管理 Evaluation Run 状态、重试及执行溯源	Dataset Version、Dataset Profile、Quality Contract、Metric Registry、Calculator Registry、执行资源配置	Logical Evaluation Plan、Physical Evaluation Plan、Executable Pipeline、Evaluation Run	Spark Catalyst、NeMo Curator、Great Expectations	P0
M5	多模态指标计算引擎	根据物理计划调度 Pipeline、Processing Stage 和 Executor；执行规则型、统计型、模型型及多模态指标算子；支持 DAG 依赖调度、并发执行、批处理、公共特征缓存、样本级计算与数据集级聚合；统一校验并持久化计算结果及证据	Physical Evaluation Plan、Sample Records、Calculator Implementation、模型服务	Metric Results、Aggregated Metric Results、计算证据、中间计算产物	Data-Juicer、TFDV、NeMo Curator	P0
M6	质量规则判定与问题检测	Rule Evaluator 根据 Quality Contract 对 Metric Results 进行确定性规则判定，生成 PASS/FAIL/UNKNOWN/SKIPPED 结果；Issue Detector 结合指标结果、规则判定及检测证据，识别缺失、重复、异常值、标注冲突等质量问题，完成问题定位、分类、去重和证据关联	Metric Results、Quality Contract、Calculator/Detector Evidence	Rule Evaluations、Quality Issues、问题证据	Cleanlab、TFDV、Soda Core	P0
M7	质量报告与可视化	汇总 Evaluation Run、Metric Results、Rule Evaluations 和 Quality Issues，生成结构化质量报告；展示指标分布、规则达标情况、问题类型、异常样本、评测覆盖率及执行溯源；支持问题下钻、报告导出和质量结论展示	Evaluation Run、Metric Results、Rule Evaluations、Quality Issues	Quality Report、质量看板、报告导出文件	Cleanlab、Great Expectations	P0
M8	Quality Agent 智能辅助	基于 Dataset Profile、评测目标和 Metric Registry 推荐指标及候选质量契约；通过 MCP Tools/Skills 调用系统能力，辅助评测策略配置、异常解释、问题诊断和修复方案生成；重要契约及修复操作由用户确认	Dataset Profile、Metric Registry、Quality Contract Templates、Quality Report、Quality Issues、Feedback Records	候选 Quality Contract、评测策略建议、问题诊断、Repair Proposal	Data-Juicer、Cleanlab、Soda Core（能力参考，Agent 编排为自主设计）	P1
M9	人工反馈、数据修复与质量复评	支持人工确认质量问题、纠正指标判断及记录审核依据；以追加式 Feedback Record 保留审核历史；管理 Repair Proposal/Action，将修复后的数据重新提交 M1 生成新 Dataset Version；复用可比的质量契约执行复评，比较版本间质量变化，并可关联受控下游任务实验验证改进效果	Quality Issues、Metric Results、Quality Reports、Feedback Records、Repair Proposal、历史与新 Dataset Version	Feedback Records、Repair Actions、Dataset Version v2、Re-evaluation Run、Version Comparison、改进验证证据	Cleanlab、Great Expectations（反馈与修复闭环为自主扩展）	P1


# 质量指标

高质量数据集的质量要求应覆盖说明文档、数据质量及模型应用三个维度的指标：
a) 说明文档：数据集的说明文档应完整，包含基本信息、内容特征、建设过程及应用说明等；
b) 数据质量：数据集中数据的质量应满足人工智能模型开发和训练的基本要求；
c) 模型应用：数据集应能有效支撑目标人工智能模型的开发和训练。


## 指南规定指标体系
维度	指标编号	子指标名称	权重	目标数据形式	指标要求 / 描述	测量方法
说明文档	0101	基本信息完整性	0.25	数据集	说明文档应包含数据集规模、格式规范、文件结构、访问渠道、技术支持方式等基本信息。	X = A / B，其中 A 为满足基本信息完整性要求的方面数量，B 为应满足的方面总数。
说明文档	0102	内容特征完整性	0.25	数据集	说明文档应包含模态类型、数据分布情况、标签类别统计、样本示例、局限性说明等内容特征。	X = A / B，其中 A 为满足内容特征完整性要求的方面数量，B 为应满足的方面总数。
说明文档	0103	建设过程完整性	0.25	数据集	说明文档应包含数据来源、采集方法、加工处理流程、标注规范、版本控制记录等建设过程。	X = A / B，其中 A 为满足建设过程完整性要求的方面数量，B 为应满足的方面总数。
说明文档	0104	应用说明完整性	0.25	数据集	说明文档应包含使用许可、目标应用场景、评估方法、基准测试结果、典型应用案例等应用说明。	X = A / B，其中 A 为满足应用说明完整性要求的方面数量，B 为应满足的方面总数。
说明文档	合计	说明文档指标得分	1.00	-	-	-
数据质量	0201	格式规范性	0.125	数据记录	数据格式符合预定标准，可直接用于模型开发和训练。预定标准可为相关标准、惯例或自定义规则。	X = A / B，其中 A 为格式符合预定标准的数据记录数量，B 为数据记录总数。
数据质量	0202	安全规范性	0.125	数据记录	数据符合模型开发和训练安全要求，不包含违反社会主义核心价值观、歧视性内容、商业违法违规、侵犯他人合法权益等非法内容。	X = A / B，其中 A 为符合安全要求的数据记录数量，B 为数据记录总数。
数据质量	0203	标注规范性	0.125	数据记录	标注符合预定标注规范，遵循预先设定的规范化流程；无监督机器学习等不需要标注的场景不适用。	X = A / B，其中 A 为符合预定标注规范的数据记录数量，B 为数据记录总数。
数据质量	0204	结构完整性	0.125	数据记录	数据填充完整，不包含缺失值，或缺失值在合理范围内。	X = A / B，其中 A 为缺失值在合理范围内的数据记录数量，B 为数据记录总数。
数据质量	0205	内容真实性	0.125	数据记录	数据真实可追溯。非生成数据可追溯到采集源头并与源头一致；生成数据可追溯到生成算法和过程，并符合目标场景真实数据分布规律。	X = A / B，其中 A 为真实可追溯的数据记录数量，B 为数据记录总数。
数据质量	0206	内容一致性	0.125	数据记录	相关联数据在语义和表达上保持匹配，包括不同模态数据间一致性和同模态数据间一致性。	X = A / B，其中 A 为内容一致的数据记录数量，B 为数据记录总数。
数据质量	0207	类型一致性	0.125	数据记录	数据符合所属数据集类型要求。通识数据集蕴含通用知识，行业通识数据集蕴含行业领域通用知识，行业专识数据集蕴含行业领域专业知识。	X = A / B，其中 A 为符合所属数据集类型要求的数据记录数量，B 为数据记录总数。
数据质量	0208	内容干净性	0.125	数据记录	数据经过严格清洗处理，不包含脏数据；分模态细分细则如下：
【文本模态】
02080101 文本困惑程度（PPL）：衡量文本语言流畅度，困惑度越小，文本越通顺自然，越符合语言表达规律。
02080102 知识信息密度：衡量文本中有效知识信息的丰富程度。
02080103 重复内容程度：衡量文档、段落、句子之间存在重复内容的程度。
02080104 文本完整程度：衡量文本内容过短或不完整的程度。
02080105 信息缺失程度：衡量标题、句子、段落等关键结构要素缺失程度。
02080106 文本纯净程度：衡量广告、页码、特殊符号、乱码等干扰内容存在程度。
02080107 文本连贯程度：衡量主题与内容一致性，以及是否存在异常截断、错别字等问题。
【图像模态】
02080201 图像分辨率：衡量图像像素尺寸大小，通常以宽 × 高像素表示。
02080202 图像重复度：衡量数据集中相同或高度相似图像的重复程度。
02080203 图像信噪比：衡量图像中有效信号与噪声的比值，数值越高表示图像质量越好。
02080204 图像清晰度：衡量视觉清晰程度，不应存在水印、抖动、字幕干扰、条纹等问题。
【视频模态】
02080301 视频分辨率：衡量视频像素尺寸大小，通常以宽 × 高像素表示。
02080302 视频重复度：衡量数据集中相同或高度相似视频的重复程度。
02080303 视频帧率（FPS）：衡量视频每秒播放帧数，帧率越高，播放越流畅。
02080304 视频时长：衡量视频播放时间长度，时长过短可能导致内容不完整或信息量不足。
02080305 视频清晰度：衡量视觉清晰程度，不应存在水印、抖动、字幕遮挡、条纹等问题。
02080306 视频动态范围：衡量视频可表现亮度和暗度的范围，HDR 视频动态范围大于 SDR 视频。
【音频模态】
02080401 音频信噪比：衡量音频信号与背景噪声的比值，信噪比越高表示音频质量越好。
02080402 信号失真比：衡量目标信号与噪声、干扰等信号的强度比值，数值越高表示音频质量越好。
02080403 音频采样率：衡量音频数据采样频率，采样率越高，音频还原度越好。
02080404 音频位深度：衡量音频数据量化精度，位深度越高，动态范围和分辨率越好。
02080405 音频比特率：衡量音频数据传输速率，比特率越高，音频质量通常越好。
02080406 音频时长：衡量音频播放时间长度，时长过短可能导致内容不完整或信息量不足。	X = A / B，其中 A 为内容干净的数据记录数量，B 为数据记录总数。
数据质量	合计	数据质量指标得分	1.000	-	-	-
模型应用	0301	内容多样性	0.25	数据集	数据分布全面程度满足目标应用场景模型开发和训练需求。	X = A / B，其中 A 为数据集分布覆盖范围，B 为目标应用场景所需的数据集分布覆盖范围。
模型应用	0302	规模完整性	0.25	数据集	数据集规模满足目标应用场景模型开发和训练所需规模。	X = A / B，其中 A 为数据集规模，B 为目标应用场景所需的数据集规模。
模型应用	0303	内容时效性	0.25	数据记录	数据采集时间和更新状态满足目标应用场景模型开发和训练要求。	X = A / B，其中 A 为满足采集时间和更新状态要求的数据记录数量，B 为数据记录总数。
模型应用	0304	标注准确性	0.25	数据记录	标注能精准标记出目标应用场景模型开发和训练所需的所有信息；无监督机器学习等不需要标注的场景不适用。	X = A / B，其中 A 为能精准标记所需全部信息的数据记录数量，B 为数据记录总数。
模型应用	0305	模型适配性	指示函数	数据集	使用数据集开发和训练的模型在目标应用场景中达到预期性能水平的程度。	若性能指标越高越好，X = A / B；若性能指标越低越好，X = B / A。其中 A 为实际模型性能，B 为预期模型性能。
模型应用	合计	模型应用指标得分	1.00	-	-	-

## 实际注册指标
- 指标编码是接口，实际指标是实现：按照指南规定指标体系的指标编号组织，可以把场景适配的指标注册到对应指标编号下，因此每个指标编号下会对应多个实际指标。比如不同模态的数据内容干净性（0208）的标准不同，就会对应多个不同的实际注册指标。
- 实际注册指标也分为数据集粒度和数据记录粒度：数据记录粒度的实际注册指标，本质是一组打标规则，满足规则的样本被标记后纳入指标计算。
- 实际注册指标和数据集对应画像有关：可以根据数据画像匹配或着定义实际注册指标

# 核心对象

流转链路：Dataset Manifest → Sample Record → Dataset Profile → Quality Contract → Evaluation Plan → Metric Result → Rule Evaluation → Quality Issue → Quality Report → Feedback Record

## 数据集清单(dataset manifest)
- 定义： 描述已注册数据集及其特定版本的身份、来源、存储位置、数据组织形式和解析配置的标准化元数据对象，是数据集管理、样本标准化、数据画像和质量评测的统一入口。
- 核心结构： 数据集标识、版本信息、原始文件清单、数据格式与模态、来源信息、Schema 映射及注册状态。
- 生成机制： 用户上传或注册数据集时自动创建 Manifest 草稿；完成文件校验与注册后发布对应版本，后续数据更新生成新版本。
- 设计原则： Manifest 只管理数据集身份及组织信息，不直接存储完整样本内容、质量评分或质量判定结果。
- 示例格式
    ```json
        {
    "dataset_id": "medical_sft",
    "dataset_version": "v1",
    "name": "Medical SFT Dataset",
    "schema_version": "1.0",

    "files": [
        {
        "file_id": "file_001",
        "uri": "datasets/medical_sft/v1/raw/train.jsonl",
        "format": "jsonl",
        "split": "train",
        "size_bytes": 52428800,
        "checksum_sha256": "example_sha256_value"
        }
    ],

    "modalities": ["text"],

    "schema_mapping": {
        "adapter_id": "sft_jsonl_adapter",
        "adapter_version": "1.0",
        "field_mapping": {
        "instruction": "instruction",
        "input": "input",
        "output": "response"
        }
    },

    "application_context": {
        "industry": "healthcare",
        "training_stage": "sft",
        "target_task": "medical_qa"
    },

    "governance": {
        "source": "user_upload",
        "license": "internal_use",
        "owner": "dataset_owner_001"
    },

    "registration": {
        "status": "registered",
        "created_at": "2026-10-15T10:00:00Z"
    }
    }
    ```


## 数据画像（dataset profiler）
- 定义： 针对特定数据集版本，通过元数据解析、结构检查和轻量级统计生成的标准化特征快照，用于描述数据集的模态、结构、规模、内容分布及应用场景。
- 生成机制： 数据集上传或发布新版本后自动触发画像任务；同一数据版本的画像结果独立保存，并记录画像算法版本，支持重新生成与版本比较。
- 核心作用： 为质量契约的模板匹配、指标适配、计算方法选择及 Agent 评测策略推荐提供依据。
- 设计原则： 画像只描述数据事实和已声明的上下文，不直接定义质量阈值或输出最终质量结论。
- 版本更新：一个数据集版本对应一份当前有效画像，但允许保留多次画像运行记录
- 唯一标识：profile_id = dataset_version_id + profiler_version + profiling_config_hash
- 示例格式：
    ```json
    {
  "profile_id": "profile_001",
  "dataset_id": "medical_sft",
  "dataset_version": "v1",
  "profile_version": "1.0",
  "status": "completed",

  "identity": {
    "sample_count": 10000,
    "format": "jsonl",
    "size_bytes": 52428800
  },

  "structure": {
    "modalities": ["text"],
    "schema": {
      "instruction": "string",
      "input": "string",
      "output": "string"
    },
    "sample_granularity": "instruction_response_pair"
  },

  "statistics": {
    "null_rates": {
      "instruction": 0.001,
      "output": 0.003
    },
    "text_length": {
      "unit": "characters",
      "mean": 380,
      "p50": 250,
      "p95": 1100
    },
    "exact_duplicate_rate": 0.025
  },

  "content_features": {
    "language_distribution": {
      "zh": 0.96,
      "en": 0.04
    },
    "domain": {
      "value": "medical",
      "source": "model_inferred",
      "confidence": 0.88
    }
  },

  "application_context": {
    "industry": "healthcare",
    "training_stage": "sft",
    "target_task": "medical_qa",
    "source": "user_declared"
  },

  "profiling_metadata": {
    "method": "full_scan",
    "profiler_version": "1.0",
    "generated_at": "2026-10-15T10:00:00Z"
  }
  }
    ```

## 样本信息（Sample Record）
- 定义： 数据集中的最小可评测数据单元，通过Adapter 解析原始数据得到的统一结构描述样本标识、模态内容、语义角色、标注信息、来源及血缘关系，为不同模态的质量指标计算提供标准化输入。
- 核心结构： 由样本身份（Identity）、内容载荷（Content）、标注信息（Annotation）、来源与血缘（Provenance）及扩展属性（Metadata）组成。
- 设计原则： 保留原始内容与业务语义，支持多模态组合和版本追溯；样本数据与质量评测结果分离存储，不因评分、规则判定或人工反馈而修改原始样本。
- 示例格式
    ```json
    {
    "id": "sample_001",
    "dataset_id": "medical_sft",
    "dataset_version": "v1",

    "rid": ["raw_001"],

    "data_content": [
        {
        "content_id": "c1",
        "media_type": ["text"],
        "role": "instruction",
        "content": "请解释高血压的常见危险因素"
        },
        {
        "content_id": "c2",
        "media_type": ["text"],
        "role": "response",
        "content": "常见危险因素包括年龄、遗传因素等。"
        }
    ],

    "annotation": {
        "label": ["medical_qa"],
        "annotation_method": "人工标注",
        "annotation_tool": "internal_platform",
        "annotator": "annotator_001"
    },

    "source": "internal",
    "source_details": "medical_qa_collection",
    "license": "internal_use",
    "generated_data_indicator": false,

    "original_time": "2026-10-01T10:00:00Z",
    "last_modified_time": "2026-10-01T10:00:00Z",

    "version": "1.0.0",
    "metadata": {
        "language": "zh"
    }
    }
    ```

## 注册指标（Metric Definition）
- 标准指标（Metric Definition）： 对齐指南规定的质量指标体系及指标编码，统一定义指标含义、适用范围和输出规范，分为通用指标与模态、行业或任务专用指标。
- 计算实现（Calculator Implementation）： 通过统一接口注册指标的具体计算方法，支持规则统计、传统机器学习、LLM 评分等；一个标准指标可对应多个计算实现。
- 使用方式： 质量契约（Quality Contract）引用已注册指标及其计算实现，针对具体数据集配置计算参数、判定规则与阈值，实现指标定义、指标计算与质量判定解耦。

## 质量契约(quality contract)
- 场景绑定：定义某类数据集、某个任务需要评哪些指标
- 一个质量契约包含多个契约指标项，每个指标项引用一个注册指标
- 契约指标项（Contract Item）
    - 定义“这个指标在当前场景下，采用什么计算配置、判定规则和阈值”
    - 可以在契约指标项中选择Metric Definition（标准指标）的Calculator Implementation（计算实现）
- 质量契约最好是可复用的场景模板，而不一定专属于某个数据集：例如，同样是中文医疗问答 SFT 数据，数据集 A 和 B 可以共用一套契约；如果 B 有特殊要求，可以从模板派生新契约版本。
- 匹配质量契约时，先使用确定性规则筛选，再由 Agent 辅助推荐
- quality Contract 与 Calculator Implementation 的关系：
    - 质量契约原则上不直接绑定具体计算实现，而是通过 Calculation Spec 声明指标计算口径、参数及实现约束，由物理计划根据 Metric Registry 选择满足条件的 Calculator Implementation。
    - 对于不影响指标语义的实现差异，由物理计划灵活选择；对于影响指标结果及可比性的模型、Prompt、Rubric 等配置，质量契约应明确约束，必要时锁定具体实现及版本。
    - 最终选定的 Calculator、模型版本、参数及执行策略统一固化到 Physical Plan，并由 Evaluation Run 记录实际执行信息，实现质量标准与执行技术解耦，同时保证评测结果可追溯、可复现。

## 评测计划(evaluation plan)
- 定义： 根据数据集版本、数据画像及质量契约，将评测需求编译为可执行的质量评测 Pipeline，参考 Spark 的逻辑计划与物理计划分层思想。
- 逻辑计划（Logical Plan）： 解析质量契约中的指标及规则，结合数据结构构建指标依赖关系，形成与执行环境无关的评测 DAG。
     ```json
        {
    "plan_id": "plan_001",
    "dataset_version_id": "dataset_v1",
    "contract_id": "sft_quality",
    "contract_version": "1.0",
    "logical_nodes": [
        {
        "node_id": "n1",
        "operator": "metric",
        "metric_code": "TEXT_LENGTH",
        "depends_on": []
        },
        {
        "node_id": "n2",
        "operator": "metric",
        "metric_code": "SEMANTIC_DUPLICATE_RATE",
        "depends_on": ["TEXT_EMBEDDING"]
        },
        {
        "node_id": "n3",
        "operator": "rule_evaluation",
        "depends_on": ["n1", "n2"]
        }
    ]
    }
    ```
- 物理计划（Physical Plan）： 根据指标注册中心的 Calculator Implementation，选择具体算子、执行策略及资源配置，构建可运行的 Pipeline。
   ```json
    {
    "plan_id": "plan_001",
    "physical_plan_version": "1.0",
    "executor": "local",
    "stages": [
        {
        "stage_id": "stage_1",
        "calculator_id": "text_length_v1",
        "batch_size": 1000,
        "device": "cpu"
        },
        {
        "stage_id": "stage_2",
        "calculator_id": "bge_embedding_v1",
        "batch_size": 32,
        "device": "cuda"
        },
        {
        "stage_id": "stage_3",
        "calculator_id": "faiss_duplicate_v1",
        "depends_on": ["stage_2"],
        "device": "cpu"
        }
    ]
    }
   ```
- 计划优化（Plan Optimization）： 通过指标依赖去重、公共计算复用、执行顺序调整和批处理等方式降低评测成本。
-执行机制： 参考 NeMo Curator 的 Pipeline、Stage、Executor 设计，由执行引擎运行物理计划，生成独立的 Evaluation Run，并记录执行状态与评测结果。

## 指标结果（Metric Result）
- 定义： 由质量评测 Pipeline 中的 Calculator Implementation 产生的标准化计算结果，记录指定数据集版本在特定指标、计算范围和计算配置下的客观测量值及其计算依据。
- 核心结构： 指标标识、评测对象、计算范围、指标值、计算状态、计算实现、参数配置、数据来源及执行溯源信息。
- 生成机制： Physical Plan 调度 Calculator 执行后生成 Metric Result，经统一 Schema 校验与持久化，供质量规则判定、问题检测和报告生成使用。
- 设计原则： 计算结果与质量判定分离，支持样本级、分组级和数据集级结果；保留计算口径与执行溯源，支持结果复用、跨版本比较和重复评测。

## 质量规则判定（Rule Evaluation）
- 定义： 根据 Quality Contract 中定义的质量规则，对 Metric Result 进行确定性判定，生成可追溯的规则执行结果。
- 核心结构： 规则标识、契约版本、关联指标结果、判定状态、实际值、规则条件及判定依据。
- 判定状态： PASS（通过）、FAIL（未通过）、UNKNOWN（无法判定）、SKIPPED（规则不适用或按策略跳过）。
- 生成机制： Rule Evaluator 读取 Metric Result 与对应 Contract Item，执行阈值比较、范围检查或组合条件判断。
- 设计原则： 规则判定与指标计算分离；同一 Metric Result 可应用于不同质量契约，不改变原始计算结果。

## 质量问题（Quality Issue）
- 定义： 质量评测过程中，由指标计算结果、质量规则判定或问题检测算子发现的、具有明确评测对象及证据的质量异常记录。
- 核心结构： 问题标识、问题类型、关联对象、严重程度、问题描述、证据、发现来源及溯源信息。
- 生成机制： Issue Detector 根据 Metric Result、Rule Evaluation 及检测器输出，识别并定位数据集级、分组级、样本级或样本对级质量问题。
- 设计原则： 问题发现与规则判定分离，支持跨指标关联、问题去重、证据追溯和人工反馈；原始检测事实不可变，审核与修复状态独立维护。

## 质量报告（Quality Report）
- 定义： 基于指定 Evaluation Run 的指标计算结果、质量规则判定及质量问题，生成数据集整体质量状况、各维度表现、主要风险及评测覆盖情况的结构化报告。
- 核心结构： 报告标识、数据集及评测版本、执行摘要、指标汇总、规则判定汇总、问题分布、质量结论及溯源信息。
- 生成机制： Report Generator 汇总 Metric Result、Rule Evaluation、Quality Issue 及 Evaluation Run 元数据，生成结构化报告，并支持可视化展示与多格式导出。
- 设计原则： 报告与底层评测事实分离，结论有据可查；支持部分评测失败、跨版本比较及报告重生成，避免将 LLM 生成的解释直接作为客观质量判定。

## 人工反馈记录（Feedback Record）
- 定义： 记录人工对质量评测结果、质量问题及评测策略的审核、纠正与建议，为质量结果校验、数据修复及评测策略优化提供可追溯的反馈依据。
- 核心结构： 反馈标识、关联对象、反馈类型、人工判断、纠正内容、审核依据、操作人员及时间信息。
- 生成机制： 用户通过质量报告、问题详情或审核工作台提交反馈，系统校验并保存不可变的反馈事件，更新相关问题的处理状态。
- 设计原则： 自动评测结果与人工反馈分离；反馈记录追加写入、不覆盖原始评测事实；支持反馈聚合、人工复核、修复验证和评测策略迭代。


# 技术选型
后端语言python+typesript，前端标准react+css+javascrpt之类的
可以用npm启动

## 选型建议
技术层	MVP 技术选型	主要用途	选型理由
开发语言	Python 3.11	评测引擎、Agent、后端服务	机器学习与数据质量生态成熟，开发成本低
API 服务	FastAPI + Pydantic v2	数据集、契约、任务、报告 API；请求和结果校验	类型清晰，便于统一 Schema 和 OpenAPI 文档
数据处理	Polars / PyArrow	JSONL、Parquet 读取、批处理和统计聚合	适合本地中小规模数据集，内存和性能较友好
数据模型	Pydantic + JSON Schema	Manifest、Sample Record、Contract、Evaluation Result	支持结构校验、序列化和接口复用
质量契约	YAML + Pydantic Validator	声明质量指标、参数、阈值和适用范围	易读、易生成、易校验，参考 Soda
指标插件	Python ABC + Registry	Metric Calculator、Issue Detector、Aggregator 的注册与调用	易于扩展和测试，参考 Data-Juicer、Cleanlab
评测编排	自研轻量 Pipeline / Stage + DAG 依赖排序	指标依赖解析、阶段执行、任务状态管理	保持最小实现，借鉴 NeMo Curator
执行方式	本地进程 + 批处理	同步小任务与后台评测任务	MVP 易部署、易调试
关系数据库	PostgreSQL + SQLAlchemy + Alembic	数据集元数据、契约、任务、指标结果、问题和反馈	事务与关联查询成熟，支持 JSONB
文件存储	本地文件系统（可扩展 MinIO）	原始数据、Parquet 结果、证据、报告文件	本地开发简单，后续可替换对象存储
规则型评测	自研基础规则 + 可选复用现有算法	缺失、格式、长度、重复、字段一致性等	保持指标结果与规则判定解耦
LLM 评分	OpenAI 兼容模型 API + 结构化输出	准确性、信息量、连贯性等多维评分	参考 Data-Juicer，可切换模型与 Prompt
轻量质量分类器	scikit-learn（TF-IDF + LogisticRegression）	大规模文本低成本质量初筛	参考 Data-Juicer GPT-3 质量分类器思路
Embedding 检测	sentence-transformers + FAISS	语义近重复与异常样本辅助检测	支持模型型质量指标和证据定位
Agent 框架	LangGraph + MCP Python SDK	Agent 决策、工具调用、评测建议和诊断	状态流程可控，工具与智能决策解耦
报告与前端	Streamlit + Plotly	数据画像、指标分布、异常样本下钻和报告展示	Python 技术栈内快速实现可交互原型
测试与工程	pytest + Ruff + uv	单元测试、规则测试、依赖管理和代码规范	保证指标、契约和结果可复现
部署	Docker Compose	PostgreSQL、API、前端及模型配置的一键部署	本地与服务器环境一致

## agent能力
- Agent / Skill：理解评测目标、推荐指标、生成候选契约、解释结果
- Contract Validator：验证契约语法、指标依赖、参数和约束合法性
- Contract Registry：管理契约版本、审批状态和适用范围
- MCP Tool：向 Agent 暴露契约校验、评测执行、报告查询等确定性操作
- Evaluation Engine：执行指标计算和规则判定
- Feedback Service：收集人工审核和修复效果

flowchart TD
    A["用户评测目标"] --> B["Agent / Skill"]
    B --> C["候选 Quality Contract"]
    C --> D["Contract Validator"]
    D --> E{"合法且可执行?"}
    E -->|否| B
    E -->|是| F["人工审批 / 策略确认"]
    F --> G["Contract Registry"]
    G --> H["MCP Tool: run_evaluation"]
    H --> I["确定性评测引擎"]
    I --> J["Quality Evaluation"]
    J --> K["Agent 诊断和修复建议"]
    K --> L["人工反馈 / 修复"]
    L --> H


## 借鉴开源项目
- Data-Juicer：https://github.com/datajuicer/data-juicer
- NeMo Curator：https://github.com/NVIDIA-NeMo/Curator
- cleanlab：https://github.com/cleanlab/cleanlab
- Great Expectations：https://github.com/fivetran/great_expectations
- TensorFlow Data Validation：https://github.com/tensorflow/data-validation
- Soda：https://github.com/sodadata/soda-core

# mvp：基于 jhsjk_article 的文本数据质量评测
## 方案
1. 验证场景
- 优先选择 MySQL 中的 jhsjk_article 文章数据表作为首个验证场景，构建面向中文文章语料的数据质量评测流程。
- 系统从 MySQL 读取文章数据，生成不可变的数据集版本，通过数据画像、质量契约、评测计划编译、指标计算、规则判定、问题检测、质量报告和人工反馈，实现一次完整的数据质量评测。
- 第一阶段聚焦文本模态，同时通过统一 Sample Record、Metric Definition、Calculator Registry 和 Pipeline 接口保留未来向图像、音频、视频及混合模态扩展的能力。

2. 数据范围
- 数据来源： MySQL jhsjk_article。
- 样本粒度： 一篇文章对应一条 Sample Record。
- 样本主键： id（UUID）；article_id 为源站文章编号，不作为全局唯一主键。
- 核心字段： article_id、title、url、pub_date、origin_name、summary、content、plain_text_content、create_time。
- 主要评测对象： 文章元数据、HTML 原文、纯文本正文及摘要。
- 数据版本： 每次评测使用固定的 MySQL 数据快照，生成 Dataset Manifest 和 Dataset Version，避免数据库后续更新影响评测复现。

3. 评测方法
MVP 选择 MySQL jhsjk_article 中文文章数据集作为验证场景，通过 Quality Contract 根据数据模态、数据集类型、目标应用场景及标注情况，确定各指标的适用性、计算口径、实现方式和质量规则。

评测采用四类计算方法：
- 确定性规则与统计计算： 对文档完整性、字段规范性、结构完整性、数据规模、时间有效性及文本长度、重复、噪声等指标进行程序化计算。
- 算法与模型计算： 对文本困惑度、知识信息密度、语义重复、文本连贯性、内容一致性等指标使用 NLP 算法、Embedding、语言模型或 LLM Judge 进行评估。
- 证据核验与人工审核： 对内容真实性、安全规范性、标注准确性及部分说明文档指标，结合来源证据、自动检测和人工审核进行判定；证据不足时不强制生成合格结论。
- 下游模型实验评测： 对内容多样性、规模完整性和模型适配性等应用相关指标，结合目标任务、参考分布、规模要求及可选的下游模型实验进行评估。

评测执行策略： 首先完整注册指南指标，随后根据 Dataset Profile 和 Quality Contract 进行适用性筛选。适用且已实现的指标执行计算；不适用指标标记为 NOT_APPLICABLE；适用但尚未实现或证据不足的指标标记为 NOT_EVALUATED，并记录原因，不得直接视为合格。

各 Calculator 输出标准化 Metric Result，Rule Evaluator 根据质量契约进行规则判定，Issue Detector 生成可定位的 Quality Issue，最终由 Report Generator 按指南维度和权重生成质量报告。

MVP 优先实现文本数据相关指标，同时保留图像、视频、音频指标定义与计算插件接口，实现指标标准全覆盖、评测能力逐步扩展。

4. 核心流程

MySQL 数据接入 → Dataset Manifest → Sample Record → Dataset Profile → Quality Contract → Logical Evaluation Plan → Physical Evaluation Plan → Evaluation Run → Metric Result → Rule Evaluation → Quality Issue → Quality Report → Feedback Record → Dataset v2 → Re-evaluation。

5. 验证目标
- 验证核心数据对象能否在各模块间标准化流转。
- 验证指标定义、计算实现及质量契约能否独立配置和复用。
- 验证逻辑计划能否编译为可执行 Pipeline，并正确处理指标依赖与结果缓存。
- 验证确定性指标、重复检测及 LLM 评测能否统一生成 Metric Result。
- 验证规则判定、问题定位、证据追溯及质量报告的完整性。
- 验证 Agent 能否辅助推荐评测策略、解释异常及生成修复建议。
- 验证人工反馈和数据版本复评能否形成可追溯的质量改进闭环。

6. 实施原则

MVP 优先保证端到端流程可运行、结果可解释、评测可复现、模块可扩展。暂不实现大规模分布式调度、自动化强化学习、完整多模态评测和自动执行高风险修复。

## 数据示例
id (主键UUID)	000019f768344a2a8682ec48341cfcba
article_id (站点文章ID（jhsjk.people.cn的article_id，用于增量去重）)	28477743
title (文章标题)	习近平出席上海合作组织成员国元首理事会第十六次会议并发表重要讲话强调弘扬上海精神 巩固团结互信 全面深化上海合作组织合作
url (文章链接)	https://jhsjk.people.cn/article/28477743
pub_date (发布日期（yyyy-MM-dd）)	2016-06-25
origin_name (来源（如：人民网－人民日报）)	人民网－人民日报
summary (列表接口返回的内容摘要)	6月24日，国家主席习近平在乌兹别克斯坦塔什干出席上海合作组织成员国元首理事会第十六次会议并发表重要讲话。这是小范围会谈开始前，成员国元首集体合影。 　　新华社记者 饶爱民摄 6月24日，国家主席习近平在乌兹别克斯坦塔什干出席上海合作组织成员国元首理事会第十六次会议并发表重要讲话。 　　新华社记者...
content (正文内容（保留HTML格式）)	<p></p> <table class="pci_c" width="100%"> <tbody> <tr> <td align="center"><img src="https://paper.people.com.cn/rmrb/res/1/20160625/1466798119024_1.jpg"></td> </tr> <tr> <td> <p style="text-indent: 2em;">6月24日，国家主席习近平在乌兹别克斯坦塔什干出席上海合作组织成员国元首理事会第十六次会议并发表重要讲话。这是小范围会谈开始前，成员国元首集体合影。<br> 　　新华社记者 饶爱民摄</p></td> </tr> </tbody> </table> <table class="pci_c" width="100%"> <tbody> <tr> <td align="center"><img src="https://paper.people.com.cn/rmrb/res/1/20160625/1466798133111_1.jpg"></td> </tr> <tr> <td> <p style="text-indent: 2em;">6月24日，国家主席习近平在乌兹别克斯坦塔什干出席上海合作组织成员国元首理事会第十六次会议并发表重要讲话。<br> 　　新华社记者 饶爱民摄</p></td> </tr> </tbody> </table> <table class="pci_c" width="100%"> <tbody> <tr> <td align="center"><img src="https://paper.people.com.cn/rmrb/res/2016-06/25/01/rmrb2016062501p22_b.jpg"></td> </tr> <tr> <td> <p style="text-indent: 2em;">6月24日，国家主席习近平在乌兹别克斯坦塔什干出席上海合作组织成员国元首理事会第十六次会议并发表重要讲话。这是大范围会谈开始前，习近平同哈萨克斯坦总统纳扎尔巴耶夫、吉尔吉斯斯坦总统阿坦巴耶夫、俄罗斯总统普京、塔吉克斯坦总统拉赫蒙、乌兹别克斯坦总统卡里莫夫，观察员国阿富汗总统加尼、白俄罗斯总统卢卡申科、印度总理莫迪、蒙古国总统额勒贝格道尔吉、巴基斯坦总统侯赛因、伊朗外交部长扎里夫，主席国客人土库曼斯坦总统别尔德穆哈梅多夫以及有关国际和地区组织代表集体合影。<br> 　　新华社记者 丁 林摄</p></td> </tr> </tbody> </table> <p style="text-indent: 2em;">人民网塔什干6月24日电&nbsp;&nbsp;（记者杜尚泽、曲颂）24日，上海合作组织成员国元首理事会第十六次会议在乌兹别克斯坦塔什干举行。国家主席习近平同哈萨克斯坦总统纳扎尔巴耶夫、吉尔吉斯斯坦总统阿坦巴耶夫、俄罗斯总统普京、塔吉克斯坦总统拉赫蒙、乌兹别克斯坦总统卡里莫夫出席会议。习近平在会上发表重要讲话，强调要坚定遵循上海合作组织根本宗旨和原则，牢牢把握发展大方向，动态调整各领域合作方略，确保本组织生机活力。</p> <p style="text-indent: 2em;">成员国元首先举行小范围会谈，随后邀请观察员国阿富汗总统加尼、白俄罗斯总统卢卡申科、印度总理莫迪、蒙古国总统额勒贝格道尔吉、巴基斯坦总统侯赛因、伊朗外交部长扎里夫，主席国客人土库曼斯坦总统别尔德穆哈梅多夫以及有关国际和地区组织代表参加大范围会谈。卡里莫夫主持会议。会议审议了上海合作组织成立15年来的主要成果。与会各方围绕上海合作组织发展及当前重大国际和地区问题交换意见，达成广泛共识。</p> <p style="text-indent: 2em;">习近平在会上发表题为《弘扬上海精神&nbsp;巩固团结互信&nbsp;全面深化上海合作组织合作》的重要讲话。</p> <p style="text-indent: 2em;">习近平指出，上海合作组织成立15年来，取得了令人瞩目的发展成就。开创性倡导和践行互信、互利、平等、协商、尊重多样文明、谋求共同发展的“上海精神”。相互坚定支持自主选择的发展道路和维护核心利益的努力，全面推进经贸、金融、能源、交通等各领域合作深入发展，扩大民间交往和人文交流，努力拓展对外交往，朋友圈日益扩大，启动首次扩员进程，国际影响力进一步提升。</p> <p style="text-indent: 2em;">习近平就上海合作组织今后发展提出5点建议。第一，弘扬“上海精神”，坚持本组织发展之本。以平等互信为基础，以互利共赢为原则，以对话协商为手段，以共同发展为目标，全面推进上海合作组织各领域合作发展，做国际秩序健康发展的建设性力量。</p> <p style="text-indent: 2em;">第二，坚持安全为先，巩固本组织发展之基。加强上海合作组织反极端主义、禁毒等法律、机制建设。中方愿在上海合作组织框架内，同其他成员国一道，为促进阿富汗民族和解和经济重建作出努力。</p> <p style="text-indent: 2em;">第三，扩大务实合作，拓展本组织发展之路。希望上海合作组织为“一带一路”建设同各国发展战略对接发挥积极作用。中方欢迎有关成员国继续推进产能合作，愿同各方一道研究促进贸易和投资便利化措施。</p> <p style="text-indent: 2em;">第四，夯实人文基础，建设本组织发展之桥。中方愿为各国开展古迹修复和考古合作提供支持，推进青年、卫生、环保等领域交流合作。</p> <p style="text-indent: 2em;">第五，坚持开放包容，壮大本组织合作队伍。各方要以这次峰会签署印度、巴基斯坦加入组织义务的备忘录为契机，共同完善组织机制建设，扩展和深化各领域合作。</p> <p style="text-indent: 2em;">习近平强调，中方愿同各方携手努力，使本组织牢牢植根于团结互信的土壤，继续成长壮大，更加枝繁叶茂。（讲话全文见第三版）</p> <p style="text-indent: 2em;">与会领导人一致同意，在相互尊重、睦邻友好原则基础上，致力于维护和平、促进发展并巩固平等互信的关系，深化各领域有效合作，为加强本地区安全稳定和可持续发展贡献力量。</p> <p style="text-indent: 2em;">领导人重申支持建设丝绸之路经济带的倡议，一致同意继续就落实这一倡议开展工作，推动上海合作组织区域经济合作，对接各国发展战略，推动交通基础设施建设合作，促进构建国际运输走廊，打造连接亚洲和欧洲的枢纽，深化经贸、产能、能源、投资、农业、文化、科技、环保等领域合作。</p> <p style="text-indent: 2em;">领导人一致同意，应在包括《联合国海洋法公约》在内的国际法原则基础上维护海洋法律秩序。所有有关争议应当由当事方通过友好谈判和协商和平解决，反对国际化和外国势力干涉。各方要恪守上述公约、《南海各方行为宣言》及落实宣言后续行动指针全部条款。</p> <p style="text-indent: 2em;">领导人认为，上海合作组织成立15年来，已成为当代国际关系体系中保障安全、稳定、可持续发展的有效因素。当前，国际形势快速变化。上海合作组织成员支持巩固联合国在国际关系中的核心作用，在联合国宪章宗旨和原则基础上完善全球治理机制，遵循不结盟、非意识形态化、不对抗的立场解决国际和地区重大问题。上海合作组织成员赞同密切合作，打击恐怖主义、分裂主义、极端主义，打击跨国有组织犯罪和现代信息技术犯罪，打击非法生产和贩运毒品。</p> <p style="text-indent: 2em;">会议决定，上海合作组织成员国元首理事会下次会议在阿斯塔纳举行。下任主席国由哈萨克斯坦担任。</p> <p style="text-indent: 2em;">成员国元首签署《上海合作组织成立十五周年塔什干宣言》以及关于批准《〈上海合作组织至2025年发展战略〉2016—2020年落实行动计划》、批准签署《关于印度共和国加入上海合作组织义务的备忘录》、批准签署《关于巴基斯坦伊斯兰共和国加入上海合作组织义务的备忘录》、批准《上海合作组织秘书长关于上海合作组织过去一年工作报告》、批准《上海合作组织地区反恐怖机构理事会关于地区反恐机构2015年工作报告》等决议，见证了《关于印度共和国加入上海合作组织义务的备忘录》和《关于巴基斯坦伊斯兰共和国加入上海合作组织义务的备忘录》等文件的签署。</p> <p style="text-indent: 2em;">会议发表了《上海合作组织成员国元首理事会会议新闻公报》。</p> <p style="text-indent: 2em;">会议后，习近平出席卡里莫夫为出席会议的各国领导人以及国际和地区组织负责人举行的欢迎宴会。</p> <p style="text-indent: 2em;">王沪宁、栗战书、杨洁篪等参加上述活动。</p> <p></p>
plain_text_content (纯文本正文内容)	6月24日，国家主席习近平在乌兹别克斯坦塔什干出席上海合作组织成员国元首理事会第十六次会议并发表重要讲话。这是小范围会谈开始前，成员国元首集体合影。 　　新华社记者 饶爱民摄 6月24日，国家主席习近平在乌兹别克斯坦塔什干出席上海合作组织成员国元首理事会第十六次会议并发表重要讲话。 　　新华社记者 饶爱民摄 6月24日，国家主席习近平在乌兹别克斯坦塔什干出席上海合作组织成员国元首理事会第十六次会议并发表重要讲话。这是大范围会谈开始前，习近平同哈萨克斯坦总统纳扎尔巴耶夫、吉尔吉斯斯坦总统阿坦巴耶夫、俄罗斯总统普京、塔吉克斯坦总统拉赫蒙、乌兹别克斯坦总统卡里莫夫，观察员国阿富汗总统加尼、白俄罗斯总统卢卡申科、印度总理莫迪、蒙古国总统额勒贝格道尔吉、巴基斯坦总统侯赛因、伊朗外交部长扎里夫，主席国客人土库曼斯坦总统别尔德穆哈梅多夫以及有关国际和地区组织代表集体合影。 　　新华社记者 丁 林摄 人民网塔什干6月24日电 （记者杜尚泽、曲颂）24日，上海合作组织成员国元首理事会第十六次会议在乌兹别克斯坦塔什干举行。国家主席习近平同哈萨克斯坦总统纳扎尔巴耶夫、吉尔吉斯斯坦总统阿坦巴耶夫、俄罗斯总统普京、塔吉克斯坦总统拉赫蒙、乌兹别克斯坦总统卡里莫夫出席会议。习近平在会上发表重要讲话，强调要坚定遵循上海合作组织根本宗旨和原则，牢牢把握发展大方向，动态调整各领域合作方略，确保本组织生机活力。 成员国元首先举行小范围会谈，随后邀请观察员国阿富汗总统加尼、白俄罗斯总统卢卡申科、印度总理莫迪、蒙古国总统额勒贝格道尔吉、巴基斯坦总统侯赛因、伊朗外交部长扎里夫，主席国客人土库曼斯坦总统别尔德穆哈梅多夫以及有关国际和地区组织代表参加大范围会谈。卡里莫夫主持会议。会议审议了上海合作组织成立15年来的主要成果。与会各方围绕上海合作组织发展及当前重大国际和地区问题交换意见，达成广泛共识。 习近平在会上发表题为《弘扬上海精神 巩固团结互信 全面深化上海合作组织合作》的重要讲话。 习近平指出，上海合作组织成立15年来，取得了令人瞩目的发展成就。开创性倡导和践行互信、互利、平等、协商、尊重多样文明、谋求共同发展的“上海精神”。相互坚定支持自主选择的发展道路和维护核心利益的努力，全面推进经贸、金融、能源、交通等各领域合作深入发展，扩大民间交往和人文交流，努力拓展对外交往，朋友圈日益扩大，启动首次扩员进程，国际影响力进一步提升。 习近平就上海合作组织今后发展提出5点建议。第一，弘扬“上海精神”，坚持本组织发展之本。以平等互信为基础，以互利共赢为原则，以对话协商为手段，以共同发展为目标，全面推进上海合作组织各领域合作发展，做国际秩序健康发展的建设性力量。 第二，坚持安全为先，巩固本组织发展之基。加强上海合作组织反极端主义、禁毒等法律、机制建设。中方愿在上海合作组织框架内，同其他成员国一道，为促进阿富汗民族和解和经济重建作出努力。 第三，扩大务实合作，拓展本组织发展之路。希望上海合作组织为“一带一路”建设同各国发展战略对接发挥积极作用。中方欢迎有关成员国继续推进产能合作，愿同各方一道研究促进贸易和投资便利化措施。 第四，夯实人文基础，建设本组织发展之桥。中方愿为各国开展古迹修复和考古合作提供支持，推进青年、卫生、环保等领域交流合作。 第五，坚持开放包容，壮大本组织合作队伍。各方要以这次峰会签署印度、巴基斯坦加入组织义务的备忘录为契机，共同完善组织机制建设，扩展和深化各领域合作。 习近平强调，中方愿同各方携手努力，使本组织牢牢植根于团结互信的土壤，继续成长壮大，更加枝繁叶茂。（讲话全文见第三版） 与会领导人一致同意，在相互尊重、睦邻友好原则基础上，致力于维护和平、促进发展并巩固平等互信的关系，深化各领域有效合作，为加强本地区安全稳定和可持续发展贡献力量。 领导人重申支持建设丝绸之路经济带的倡议，一致同意继续就落实这一倡议开展工作，推动上海合作组织区域经济合作，对接各国发展战略，推动交通基础设施建设合作，促进构建国际运输走廊，打造连接亚洲和欧洲的枢纽，深化经贸、产能、能源、投资、农业、文化、科技、环保等领域合作。 领导人一致同意，应在包括《联合国海洋法公约》在内的国际法原则基础上维护海洋法律秩序。所有有关争议应当由当事方通过友好谈判和协商和平解决，反对国际化和外国势力干涉。各方要恪守上述公约、《南海各方行为宣言》及落实宣言后续行动指针全部条款。 领导人认为，上海合作组织成立15年来，已成为当代国际关系体系中保障安全、稳定、可持续发展的有效因素。当前，国际形势快速变化。上海合作组织成员支持巩固联合国在国际关系中的核心作用，在联合国宪章宗旨和原则基础上完善全球治理机制，遵循不结盟、非意识形态化、不对抗的立场解决国际和地区重大问题。上海合作组织成员赞同密切合作，打击恐怖主义、分裂主义、极端主义，打击跨国有组织犯罪和现代信息技术犯罪，打击非法生产和贩运毒品。 会议决定，上海合作组织成员国元首理事会下次会议在阿斯塔纳举行。下任主席国由哈萨克斯坦担任。 成员国元首签署《上海合作组织成立十五周年塔什干宣言》以及关于批准《〈上海合作组织至2025年发展战略〉2016—2020年落实行动计划》、批准签署《关于印度共和国加入上海合作组织义务的备忘录》、批准签署《关于巴基斯坦伊斯兰共和国加入上海合作组织义务的备忘录》、批准《上海合作组织秘书长关于上海合作组织过去一年工作报告》、批准《上海合作组织地区反恐怖机构理事会关于地区反恐机构2015年工作报告》等决议，见证了《关于印度共和国加入上海合作组织义务的备忘录》和《关于巴基斯坦伊斯兰共和国加入上海合作组织义务的备忘录》等文件的签署。 会议发表了《上海合作组织成员国元首理事会会议新闻公报》。 会议后，习近平出席卡里莫夫为出席会议的各国领导人以及国际和地区组织负责人举行的欢迎宴会。 王沪宁、栗战书、杨洁篪等参加上述活动。
create_time (创建时间)	2026-08-05 14:44:25

