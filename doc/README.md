# 摘星 · 微信助手公开文档

> 本目录面向用户、部署人员和希望了解项目的开发者。内容以当前源码和实际界面为准，避免暴露本地环境、凭据及底层数据访问细节。

## 快速入口

- [上手指南](getting-started.md)
- [项目架构](architecture.md)
- [AI 后端与降级](modules/ai-backend.md)
- [Agent 助手](modules/agent.md)
- [群聊摘要](modules/group-digest.md)
- [公众号助手](modules/oa-assistant.md)
- [定时任务与 Skill](modules/cron-scheduler.md)
- [通知队列](modules/notification-outbox.md)
- [关键词提醒](modules/keyword-alert.md)
- [消息推送](modules/ilink-push.md)
- [MCP 扩展](modules/mcp-client.md)
- [Skill 体系](modules/skill.md)

## 产品定位

摘星是一款本地运行的微信助手，提供消息浏览、收藏与朋友圈管理、关键词提醒、群聊和公众号摘要、AI 对话、Agent 自动化以及多渠道通知。核心数据默认留在本机，用户可以按需启用云端 AI 服务和消息推送渠道。

## 主要能力

| 能力 | 是否必须配置 AI |
|---|---:|
| 消息浏览、搜索和导出 | 否 |
| 收藏、朋友圈浏览和导出 | 否 |
| 关键词提醒 | 否 |
| 公众号文章读取和即时提醒 | 否（AI 速读为增强项） |
| 群聊/公众号定时摘要 | 是 |
| 收藏、聊天、朋友圈 AI 对话 | 是 |
| Agent 和 prompt 类型 Skill | 是 |
| script 类型 Skill | 否 |
| 本地语义检索 | 不依赖云端 AI，但需要本地模型 |

## 文档说明

- `doc/`：适合公开阅读的产品、架构和使用说明。
- `docs/`：项目私有技术笔记，不作为公开文档引用。
- 文档中的 API、界面名称和配置字段可能随版本更新；遇到冲突时以当前源码和实际界面为准。
