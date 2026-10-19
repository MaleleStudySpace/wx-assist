# RAG 启动恢复与索引重建

## 故障表现与原因

已有数据时重启，窗口可能未响应，日志停在 RAG 冷启动附近。曾在真实索引中复现
ChromaDB 1.5.9 的 `collection.count()` / HNSW 恢复卡住：底层调用占住 GIL，
连主线程也无法运行。SQLite `quick_check` 通过不代表向量索引正常。
这不能单凭日志归因于某次强制退出，也不是中文安装目录导致。

## 运行时保护

- ChromaDB 由独立子进程持有；启动时实际读取、检索已有索引。
- 每次通信默认最多等 30 秒，故障后结束子进程并禁用当前实例，其他功能继续运行。
- 查询失败返回空结果；索引写入失败向上抛出，不保存虚假的完成进度。
- 消息分块使用稳定 ID 并以 upsert 写入，失败重试不会重复添加相同分块。
- 模型编码采用小批次并串行执行，降低多任务同时索引时的内存峰值。

## 离线重建

先退出桌面应用。使用安装了 `chromadb`、`fastembed` 的 Python 环境，
并将嵌入模型放在项目的 `models/` 缓存目录。运行：

```powershell
.venv\Scripts\python.exe scripts\rebuild_rag.py --data-dir 'D:\微信助手\data'
```

工具通过 SQLite backup 获取消息快照，在新目录重建最近 30 天的消息、公众号、
朋友圈和收藏索引，不访问 AI API 或发送消息。关闭并重新打开索引验证通过后，
才替换 `chroma/`、`rag_state.json`、`last_indexed.json`。原消息数据库和配置不变。
原索引及游标保留在 `rag-rebuild-时间/previous/`，可在退出应用后一起恢复。
重建失败不会替换活动索引，失败原因输出到终端。

## 验证

```powershell
.venv\Scripts\python.exe -m pytest tests/test_rag_recovery.py -q
```

覆盖子进程卡住/退出、启动超时、重启后查询、幂等更新，以及失败后的索引进度。
测试使用合成数据，不提交真实数据库、日志、密钥或模型文件。
