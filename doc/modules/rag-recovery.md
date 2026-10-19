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
朋友圈和收藏索引，使用该数据目录保存的计算设备与批次设置，不访问 AI API 或发送消息。
若检测到该目录中的 EXE 仍在运行，会拒绝开始。关闭并重新打开索引验证通过后，
才替换 `chroma/`、`rag_state.json`、`last_indexed.json`。原消息数据库和配置不变。
原索引及游标保留在 `rag-rebuild-时间/previous/`，可在退出应用后一起恢复。
重建失败不会替换活动索引，失败原因输出到终端。

## 验证

```powershell
.venv\Scripts\python.exe -m pytest tests/test_rag_recovery.py -q
```

覆盖子进程卡住/退出、启动超时、重启后查询、幂等更新，以及失败后的索引进度。
测试使用合成数据，不提交真实数据库、日志、密钥或模型文件。

## 进度与显卡加速

运行状态和「系统配置 → 数据配置」显示聊天、公众号、朋友圈、收藏各自的索引进度。
进度表示已成功处理的当前任务；缓存同步发现新内容后会开始新的增量任务。
模型加载、错误和实际计算设备单独显示。故障不会伪装成“已完成”。

设置中可选择自动、CPU、DirectML、CUDA，以及每批 8/16/32/64 项。
自动模式优先 CUDA，其次 DirectML，最后 CPU。GPU 初始化失败、驱动不匹配或
编码时显存不足会记录原因并回退 CPU；界面显示实际后端，不仅是用户选择。
2 GB 显卡建议每批 8 项；显卡加速只加速向量编码，索引存储仍使用 CPU。

Windows 推荐 DirectML（支持兼容 DirectX 12 的 NVIDIA/AMD/Intel 显卡）：

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -File scripts/setup-rag.ps1 -Device directml
```

CPU 使用 `-Device cpu`，CUDA 使用 `-Device cuda`；CUDA 还需要与 ONNX Runtime
匹配的 NVIDIA 驱动、CUDA/cuDNN 运行库。安装脚本仅修改指定虚拟环境。
三个 ONNX 包共享模块文件，必须只保留一种；安装其他依赖后若重新装入 CPU 包，
重新运行脚本恢复目标后端。FastEmbed 的依赖元数据仍声明 CPU 包，故 GPU 环境
的 `pip check` 可能报告缺少 `onnxruntime`，以实际 provider 和编码测试为准。

发布 EXE 时在对应环境执行 `pyinstaller build_rag.spec --noconfirm`，将运行库和
模型一并打包；普通用户选择加速模式即可，不需要自行安装 Python/CUDA（DirectML 版）。
只有 CPU 运行库的旧 EXE 不会因为设置选择 GPU 就获得加速能力。

打包后可运行 `wx-assist.exe --check-rag D:\rag-check`。这会使用合成文本测试
模型、子进程、索引写入及重启查询，将结果写入该目录的 `result.json`；
不启动微信连接、推送或 AI API。建议 CPU/DirectML 发布包分别通过此检查再分发。
