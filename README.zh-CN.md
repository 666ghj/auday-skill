<p align="center">
  <img src="docs/assets/auday-banner.png" alt="Auday" width="100%">
</p>

<h1 align="center">Auday Skill</h1>

<p align="center">把 Auday 所知，接入你的工作流</p>

<p align="center">
  <a href="README.md">English</a> &nbsp;·&nbsp; <strong>简体中文</strong> &nbsp;·&nbsp; <a href="README.ja.md">日本語</a>
</p>

**Auday 把你的 Apple Watch 变成全天候 AI 录音助手。** 它结合录音、心率等身体信号、实时压力估计，以及位置和活动信息，在 iPhone 上串联一天的经历。通过 Highlights、每日反思和可回溯原文的 AI 对话，帮助你回顾重要时刻、发现关联，逐渐积累属于自己的记忆。

<p align="center">
  <a href="https://apps.apple.com/app/id6814210373">
    <img src="https://toolbox.marketingtools.apple.com/api/badges/download-on-the-app-store/black/zh-cn" alt="在 App Store 下载" height="40">
  </a>
</p>

<p align="center"><a href="https://auday.ai/">访问官网 auday.ai</a></p>

这个仓库提供 **Auday 的桌面端 Skill**，将 App 中已保存的资料接入你常用的 AI 助手，用于检索、写作，以及借助 Windows 或 Mac 的本地算力进行更深入的音频分析。Windows 11 ARM64 电脑也在支持范围内。手机数据始终只读，使用的信息保留来源。

**iPhone 与 Windows 或 Mac 电脑通过本地加密协议直接传输 Auday 数据，不经过任何云端服务器中转。**

## 通过桌面 Agent 安装

打开支持本地 Skill 的桌面 Agent，发送：

> 请从 https://github.com/666ghj/auday-skill 为当前软件安装 Auday Skill，完成首次配置，推荐准备本地语义检索，再引导我连接手机上的 Auday。转写工具等我需要时再安装。

不同软件的 Skill 安装目录与导入方式可能不同。请让 Agent 安装到当前软件支持的位置，并确认软件能够发现和使用它。

Agent 会使用所在软件的原生 Skill 安装工具，检查电脑的环境并准备所需工具。你不必先自行安装全局 Python，也不用照着命令逐条操作。Windows 不需要 WSL、Homebrew、MSVC 编译工具或 GPU；Mac 不需要 Homebrew。如果 macOS 需要系统密码或许可确认，Agent 会解释对应的系统步骤，完成后继续。

首次使用时，Agent 会推荐一次**本地语义检索**：下载 333.59 MB 模型，以及 Mac 约 11.2 MB、Windows x64 约 18.43 MB、Windows ARM64 约 11.99 MB 的运行程序压缩包。Windows 还会按需准备独立 Microsoft 运行时包：x64 约 3.22 MB，ARM64 约 5.05 MB，不做系统级安装。如需准备基础环境，还会有额外下载，大小因操作系统而异。解压文件和后续索引需要额外空间。你可以现在安装或稍后再装，选择会被保存，不会重复提示；不安装也可以使用关键词检索，之后可随时要求 Agent 准备语义检索。模型使用须遵循 [Gemma 条款](https://ai.google.dev/gemma/terms)。

**转写按需准备。** 当你提出转写需求，Agent 会选择一套合适的模型，完成下载、校验、配置和运行验证。默认包含匿名说话人分段；直接说“不要说话人识别”，就会跳过相关模型和依赖。

下载和准备过程中会展示当前阶段与实际进度。Agent 会分别说明哪些工具已经可用、手机是否已连接、首次同步是否完成。完整执行流程见[首次使用指南](references/first-run.md)。

如果宿主软件没有原生安装工具，Agent 可以将安装包复制到当前软件支持的 Skill 目录，或在已有 Python 的环境中使用仓库附带的本地安装器。安装器只复制 Skill 运行代码、参考资料、元数据、许可证与版本信息，不负责准备模型，不覆盖已有安装，也不修改宿主软件配置。如果软件没有发现新 Skill，可以刷新或重启。随后由 Agent 按操作系统运行已安装的首次配置脚本：Windows 使用 PowerShell 5.1 执行 `scripts/bootstrap.ps1`，Mac 使用 `scripts/bootstrap.sh`。两个流程都不要求预先安装全局 Python；具体安装与配置步骤见[首次使用指南](references/first-run.md)。

## 连接手机

连接前请确认：iPhone 和电脑连接同一个 Wi-Fi；系统询问时允许**本地网络**访问；传输时保持手机接入页在前台打开。

1. 在 iPhone 上打开「设置 → 接入你的工作流」。
2. 选择「全部文字」或「文字与音频」，然后开启接入。两档权限都只能读取。
3. 请桌面 Agent 连接 Auday，在本地输入窗口填写手机生成的通信密码，不要将密码发到聊天中。
4. 同步期间保持手机页面在前台，再让 Agent 使用已获得的上下文。

你可以这样提问：

- 「我们最后怎么决定发布安排的？给我对应的原始讨论。」
- 「把这周的项目讨论整理成一份交接简报。」
- 「回顾今天的记忆，把明确承诺和可能的后续事项分开。」
- 「借助电脑分析这段录音，保留它与原始时间线的对应关系。」

Agent 可以用关键词查找原词，也可以用[本地语义检索](references/semantic-search.md)查找概念和不同表述。如果首次配置时选择了语义检索，Agent 会在第一次同步后建立持久索引，不会把文字发送给外部 Embedding API。同步、建索引和音频分析时会展示当前阶段及实际处理数量。

同步默认使用增量模式：连接支持新协议的手机版本后，先核对完整的轻量清单，只下载新增、修改或电脑端已损坏的文字正文。也可以要求 Agent 全量刷新，对应 `sync --mode full`。旧版手机端会明确提示回退全量；首次建立校验基线仍需完整下载一次。手机准备快照的过程目前仍需处理完整资料。

同步资料保存在硬盘上的 SQLite 数据库中，重启电脑后仍然存在。每次同步还会更新[长期维护的本地文件库](references/library.md)：提供总目录、按日期和类型整理的记录、完整来源信息，以及独立的 `notes/` 用户笔记区。未变化的文件复用，已移除的来源和手动修改的托管文件另行保留。已有数据库可以通过 `library` 离线生成文件库，也仍可[导出独立的静态快照](references/context.md#readable-files)。

## 运行要求

- **Windows：**支持 Windows 10/11 x64 和 Windows 11 ARM64。Agent 通过 PowerShell 5.1 配置脚本检测系统的原生架构，准备专用的 `uv` 0.13.3、原生 Python 3.13.16 和对应架构的本地模型运行程序。以 CPU 运行为基础，不要求 GPU、WSL、Homebrew、MSVC 编译工具或已有全局 Python。
- **Mac：**基础客户端需要 macOS 13 或更新版本；指定的语义运行程序需要 macOS 13.3 或更新版本。手机连接和内置音频工具构建需要带有 Swift 5.9 或更新版本的 Xcode Command Line Tools。Agent 会检查，缺失时可启动 Apple 安装器；需要你本人完成的系统对话框仍在本机处理。首次配置脚本按需准备专用的 `uv` 和受管理的 Python，不要求已有全局 Python 或 Homebrew。
- Linux 和 Windows 10 ARM64 暂不在支持范围内。可选本地模型另有处理器要求，配置时会分别验证。
- 带有「接入你的工作流」功能的 Auday iPhone 版本，iPhone 和电脑连接同一个 Wi-Fi。
- 模型不随安装包提供，由 Agent 在所选能力的配置流程中下载、校验哈希，并确认本地可运行。

基本连接与上下文读取不需要 AI API Key、Auday 云端账号或云端中继。

需要深入分析录音时，[音频处理指南](references/audio.md)介绍 Windows 与 Mac 的处理流程，[语言策略指南](references/audio-language-routing.md)会指导 Agent 按日语、中文及方言、英语、韩语、法语、德语和多语言混合等情况选择方案；[Mac 音频指南](references/mac-audio-models.md)仅针对 Mac，补充说话人分析与资源安排。Agent 可以根据实际录音调整工具，也可以复用已经适合你的方案。

内置转写配置可按电脑和任务选择多语言 Whisper `small` Q5_1（190.09 MB）或 large-v3-`turbo` Q5_0（574.04 MB），默认另配约 30 MB 的说话人模型；运行程序和依赖还需额外下载。Agent 只安装选定的一套并在之后复用，指南中的其他引擎需单独配置。匿名说话人标签不代表真实身份识别。Windows x64 的本地转写运行程序要求 CPU 支持 AVX2、FMA 和 F16C；ARM64 使用独立的原生引擎。若某个可选模型运行程序与处理器不兼容，基础连接、文字同步、关键词检索和可读文件导出仍可使用。

## 数据与隐私

本地连接程序使用系统生成的 16 位通信密码、HKDF-SHA256，以及 TLS 1.2 ECDHE-PSK 与 ChaCha20-Poly1305。通信双方通过共享密钥认证；它不接受任意短口令，也不会回退到明文传输。具体边界与现有限制见[协议文档](docs/protocol.md)。

通信密码保存在 Windows Credential Manager 或 macOS Keychain 中。文字数据库默认保存在以下持久文件中，不在 Skill 安装目录内：

| 操作系统 | 默认数据库路径 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\AudayWorkflow\default\library.sqlite` |
| Mac | `~/Library/Application Support/AudayWorkflow/default/library.sqlite` |

模型、配置和索引也保存在本机。先同步文字，需要时再获取音频。关闭接入可以阻止后续读取，但无法收回其他设备已经下载的副本。

在支持按需准备的手机版本中，开启接入后即可接受连接，电脑请求同步时才准备资料快照。较大的文字记录通过可续传、带校验的分块完整获取，保留已保存的内容与来源关联。当前 Skill 也兼容提前准备快照的旧版手机端。

本地传输不代表云端 AI 助手也在本地推理。应只向当前任务返回必要片段；若要让数据全程留在本机，模型推理和后续工具也必须在本地运行。

## 开发与验证

已记录的安装验证范围与边界见[首次安装验收记录](docs/first-run-validation.md)。Windows 运行时使用受管理的 Python 3.13.16，CI 分别覆盖原生 x64 和 ARM64 环境。CI 或合成连接测试通过，不代表 Windows 与真实 iPhone 的局域网连接已通过实机验证。

以下命令用于 Mac 开发环境，其中 Swift 测试仅适用于 Mac：

```sh
sh tools/git/install-hooks.sh
python3 tools/verify_public.py
python3 -m unittest discover -s tests -v
swift test --package-path scripts/link
swift test --package-path tests/native-peer
```

测试使用合成数据，覆盖加密连接、只读权限、快照一致性、下载续传与离线查询。手机连接验证见[实机测试说明](docs/device-testing.md)。Mac 的 Keychain 测试需显式开启：`AUDAY_TEST_KEYCHAIN=1 python3 -m unittest discover -s tests -p test_pairing.py -v`。默认测试不需要真实录音，也不下载模型。


## 许可证

采用 MIT 许可证，详见 [LICENSE](LICENSE) 和[第三方说明](THIRD_PARTY_NOTICES.md)。可选依赖和模型权重分别遵循各自的许可证。
