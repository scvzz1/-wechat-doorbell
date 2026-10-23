# Third-party components

This repository vendors one third-party component:

## weixin-bot (Python SDK + protocol documentation)

- Location: `weixin-bot/` (the `weixin_bot` Python package and
  `docs/protocol-spec.md`)
- Upstream: https://github.com/epiral/weixin-bot
- License: MIT (declared in the upstream project's pyproject.toml)
- What we use it for: the iLink protocol client — QR login, long-polling
  message receive, config/typing endpoints, and the protocol specification
  the media pipeline in this repository was implemented against.

The bridge daemon, media send/receive extensions, transcription pipeline,
brain adapters, and documentation in this repository are original work,
licensed under the MIT License (see LICENSE).

## Protocol

The wire protocol itself is Tencent's iLink bot protocol
(ilinkai.weixin.qq.com), the same one used by the official
@tencent-weixin/openclaw-weixin plugin. This project is not affiliated
with Tencent.
