<p align="center">
  <img src="docs/assets/auday-banner.png" alt="Auday" width="100%">
</p>

<h1 align="center">Auday Skill</h1>

<p align="center">把 Auday 所知，接入你的工作流。</p>

<p align="center">
  <a href="README.md">English</a> &nbsp;·&nbsp; <strong>简体中文</strong> &nbsp;·&nbsp; <a href="README.ja.md">日本語</a>
</p>

让你常用的桌面 AI 助手查找讨论、关联记忆、整理项目简报，或借助 Mac 的算力深入分析录音。手机数据始终只读，使用的信息保留来源。

## 通过桌面 Agent 安装

打开支持本地 Skill 的桌面 Agent，发送：

> 请从 https://github.com/666ghj/auday-skill 为当前软件安装 Auday Skill，确认可用后，引导我连接手机上的 Auday。

不同软件的 Skill 安装目录与导入方式可能不同。请让 Agent 安装到当前软件支持的位置，并确认软件能够发现和使用它。

Agent 可以使用所在软件的原生 Skill 安装工具。仓库也提供行为明确的本地安装器：

```sh
python3 tools/install.py --destination ~/.agents/skills/auday
```

请使用当前软件支持的安装目录。安装器只复制 Skill 运行代码、参考资料、元数据、许可证与版本信息，不覆盖已有安装，也不修改宿主软件配置。如果软件没有发现新 Skill，可以刷新或重启。

## 连接手机

1. 在 iPhone 上打开「设置 → 接入你的工作流」。
2. 选择「全部文字」或「文字与音频」，然后开启接入。两档权限都只能读取。
3. 请桌面 Agent 连接 Auday，在本地输入窗口填写手机生成的通信密码，不要将密码发到聊天中。
4. 同步期间保持手机页面在前台，再让 Agent 使用已获得的上下文。

你可以这样提问：

- 「我们最后怎么决定发布安排的？给我对应的原始讨论。」
- 「把这周的项目讨论整理成一份交接简报。」
- 「回顾今天的记忆，把明确承诺和可能的后续事项分开。」
- 「借助 Mac 分析这段录音，保留它与原始时间线的对应关系。」

## 运行要求

- macOS 13 或更新版本、Python 3.9 或更新版本，以及带有 Swift 5.9 或更新版本的 Xcode Command Line Tools。
- 带有「接入你的工作流」功能的 Auday iPhone 版本，以及可互通的本地网络。Apple 点对点发现仍属实验能力，普通局域网连接不依赖它。
- 可选音频分析：`ffmpeg`、`whisper-cli`、本地模型权重，以及通过 `uv` 管理的 Python 3.11–3.13 说话人分析依赖。模型不随仓库提供，也不会自动下载。

基本连接与上下文读取不需要 AI API Key、Auday 云端账号或云端中继。

需要深入分析录音时，[语言策略指南](references/audio-language-routing.md)会指导 Agent 按日语、中文及方言、英语、韩语、法语、德语和多语言混合等情况选择方案；[Mac 音频指南](references/mac-audio-models.md)补充说话人分析与资源安排。Agent 可以根据实际录音调整工具，也可以复用已经适合你的方案。

## 数据与隐私

原生连接程序使用系统生成的 16 位通信密码、HKDF-SHA256，以及 TLS 1.2 ECDHE-PSK 与 ChaCha20-Poly1305。它不接受任意短口令，不跳过证书验证，也不会回退到明文传输。具体边界与现有限制见[协议文档](docs/protocol.md)。

通信密码保存在 macOS Keychain 中。本地缓存位于用户的应用数据目录，不在源码仓库内。先同步文字，需要时再获取音频。关闭接入可以阻止后续读取，但无法收回其他设备已经下载的副本。

本地传输不代表云端 AI 助手也在本地推理。应只向当前任务返回必要片段；若要让数据全程留在本机，模型推理和后续工具也必须在本地运行。

## 开发与验证

```sh
sh tools/git/install-hooks.sh
python3 tools/verify_public.py
python3 -m unittest discover -s tests -v
swift test --package-path scripts/link
swift test --package-path tests/native-peer
```

测试使用合成数据，覆盖加密连接、只读权限、快照一致性、下载续传与离线查询。手机连接验证见[实机测试说明](docs/device-testing.md)。Keychain 测试需显式开启：`AUDAY_TEST_KEYCHAIN=1 python3 -m unittest discover -s tests -p test_pairing.py -v`。默认测试不需要真实录音，也不下载模型。


## 许可证

采用 MIT 许可证，详见 [LICENSE](LICENSE) 和[第三方说明](THIRD_PARTY_NOTICES.md)。可选依赖和模型权重分别遵循各自的许可证。
