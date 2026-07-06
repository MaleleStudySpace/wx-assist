"""
下载 BGE-small-zh-v1.5 ONNX 模型到本地 models/ 目录。

用法:
    D:\Python313\python.exe scripts/download_rag_model.py

需要网络连接 HuggingFace。
如果连接失败，手动下载地址见脚本注释。
"""

import os
import sys
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = PROJECT_ROOT / "models" / "bge-small-zh-v1.5"
CACHE_DIR = PROJECT_ROOT / "models"


def download_via_fastembed():
    """方式一：用 fastembed 下载（自动处理目录结构）"""
    print("正在通过 fastembed 下载 BGE-small-zh-v1.5 模型...")
    print(f"缓存目录: {CACHE_DIR}")
    print("如果长时间卡住，可能是网络问题，按 Ctrl+C 取消后联系我。")

    from fastembed import TextEmbedding

    # cache_dir 指向 models/ 目录
    model = TextEmbedding(
        model_name="BAAI/bge-small-zh-v1.5",
        cache_dir=str(CACHE_DIR),
    )
    # 预热触发下载
    list(model.embed(["预热"]))
    print("下载完成！")
    print(f"模型位置: {CACHE_DIR}")
    return True


def download_manual_files():
    """方式二：手动下载说明（备选）"""
    print()
    print("=" * 60)
    print("如果自动下载失败，可以手动下载后放入 models/bge-small-zh-v1.5/")
    print()
    print("需要的文件:")
    print("  https://huggingface.co/Qdrant/bge-small-zh-v1.5/resolve/main/onnx/model.onnx")
    print("  https://huggingface.co/Qdrant/bge-small-zh-v1.5/resolve/main/onnx/config.json")
    print("  https://huggingface.co/Qdrant/bge-small-zh-v1.5/resolve/main/tokenizer.json")
    print("  https://huggingface.co/Qdrant/bge-small-zh-v1.5/resolve/main/tokenizer_config.json")
    print("  https://huggingface.co/Qdrant/bge-small-zh-v1.5/resolve/main/special_tokens_map.json")
    print()
    print("手动下载后放置结构:")
    print("  models/bge-small-zh-v1.5/")
    print("    ├── onnx/model.onnx           (~33MB)")
    print("    ├── onnx/config.json")
    print("    ├── tokenizer.json")
    print("    ├── tokenizer_config.json")
    print("    └── special_tokens_map.json")
    print("=" * 60)


if __name__ == "__main__":
    os.makedirs(MODEL_DIR, exist_ok=True)

    try:
        download_via_fastembed()
    except Exception as e:
        print(f"\n自动下载失败: {e}")
        download_manual_files()
        sys.exit(1)

    print("\n现在你可以通过以下方式验证:")
    print(f'  D:\\Python313\\python.exe -c "from fastembed import TextEmbedding; m = TextEmbedding(\'BAAI/bge-small-zh-v1.5\', cache_dir=r\'{CACHE_DIR}\'); print(list(m.embed([\'测试\'])))"')
