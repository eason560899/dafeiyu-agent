# Third-party notices

This project is an independent integration layer. It does not bundle third-party platform clients or credentials.

The implementation and documentation were informed by these MIT-licensed projects:

- [OpenBiliClaw](https://github.com/whiteguo233/OpenBiliClaw)
- [OpenBiliClaw-mobile](https://github.com/whiteguo233/OpenBiliClaw-mobile)
- [bili-cli](https://github.com/kukemc/bili-cli)
- [Tulpa](https://github.com/fumingyang2004/Tulpa)
- [dsh-im](https://github.com/xmanrui/dsh-im)

参考边界：OpenBiliClaw 提供本地优先的跨平台内容发现思路；`bili-cli` 仅作为可选的 B 站命令行运行时；Tulpa 提供通过 MCP 暴露 QQ、微信和 B 站能力的独立桌面工具；`dsh-im` 是把多个 IM 机器人接入 DeepSeek Harness 的独立插件。本文项目只保留自己的配置型适配器和 OpenAI-compatible provider，不捆绑上述项目的源码、SDK、账号凭据或运行时，也不保证它们之间的协议兼容。微信适配器中的 `bridge_url` 只是部署方提供的外部桥接地址，不代表本项目直接实现 Tencent iLink 或 `dsh-im` 协议。

The project also references the behavior and safety concepts of [dsh-bili-scroll](https://github.com/c1overzz/dsh-bili-scroll). Its source is not copied into this repository.

Users are responsible for installing and complying with the licenses and terms of any optional connector, browser automation runtime, platform client, model provider, or account they connect.

