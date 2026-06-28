"""
微信朋友圈读取工具 — 基于 WcdbNativeClient 的 DLL 接口

重构：不再独立加载 DLL / 做 DRM patch，改为接受一个 WcdbNativeClient 实例，
通过 client.get_sns_timeline() / client.exec_query() 委托查询。
"""
import json
import os
import struct
from pathlib import Path
from datetime import datetime


# ── ISAAC-64 解密（朋友圈图片加密） ──────────────────────────────

class Isaac64:
    MASK = (1 << 64) - 1
    def __init__(self, seed):
        self.mm = [0] * 256
        self.aa = self.bb = self.cc = 0
        self.randrsl = [0] * 256
        self.randcnt = 0
        self.randrsl[0] = seed & self.MASK
        self._init(True)

    def _m(self, v):
        return v & self.MASK

    def _init(self, flag):
        a = b = c = d = e = f = g = h = 0x9E3779B97F4A7C15 & self.MASK

        def mix():
            nonlocal a, b, c, d, e, f, g, h
            a = self._m(a - e); f ^= (h >> 9); h = self._m(h + a)
            b = self._m(b - f); g ^= (self._m(a << 9)); a = self._m(a + b)
            c = self._m(c - g); h ^= (b >> 23); b = self._m(b + c)
            d = self._m(d - h); a ^= (self._m(c << 15)); c = self._m(c + d)
            e = self._m(e - a); b ^= (d >> 14); d = self._m(d + e)
            f = self._m(f - b); c ^= (self._m(e << 20)); e = self._m(e + f)
            g = self._m(g - c); d ^= (f >> 17); f = self._m(f + g)
            h = self._m(h - d); e ^= (self._m(g << 14)); g = self._m(g + h)

        for _ in range(4): mix()
        for i in range(0, 256, 8):
            if flag:
                a = self._m(a + self.randrsl[i])
                b = self._m(b + self.randrsl[i + 1])
                c = self._m(c + self.randrsl[i + 2])
                d = self._m(d + self.randrsl[i + 3])
                e = self._m(e + self.randrsl[i + 4])
                f = self._m(f + self.randrsl[i + 5])
                g = self._m(g + self.randrsl[i + 6])
                h = self._m(h + self.randrsl[i + 7])
            mix()
            self.mm[i] = a; self.mm[i+1] = b; self.mm[i+2] = c; self.mm[i+3] = d
            self.mm[i+4] = e; self.mm[i+5] = f; self.mm[i+6] = g; self.mm[i+7] = h
        if flag:
            for i in range(0, 256, 8):
                a = self._m(a + self.mm[i])
                b = self._m(b + self.mm[i + 1])
                c = self._m(c + self.mm[i + 2])
                d = self._m(d + self.mm[i + 3])
                e = self._m(e + self.mm[i + 4])
                f = self._m(f + self.mm[i + 5])
                g = self._m(g + self.mm[i + 6])
                h = self._m(h + self.mm[i + 7])
                mix()
                self.mm[i] = a; self.mm[i+1] = b; self.mm[i+2] = c; self.mm[i+3] = d
                self.mm[i+4] = e; self.mm[i+5] = f; self.mm[i+6] = g; self.mm[i+7] = h
        self._isaac64()
        self.randcnt = 256

    def _isaac64(self):
        self.cc = self._m(self.cc + 1)
        self.bb = self._m(self.bb + self.cc)
        for i in range(256):
            x = self.mm[i]
            mod = i & 3
            if mod == 0: self.aa = self._m(self.aa ^ (self._m(self.aa << 21) ^ self.MASK))
            elif mod == 1: self.aa = self._m(self.aa ^ (self.aa >> 5))
            elif mod == 2: self.aa = self._m(self.aa ^ (self._m(self.aa << 12)))
            else: self.aa = self._m(self.aa ^ (self.aa >> 33))
            self.aa = self._m(self.mm[(i + 128) & 255] + self.aa)
            y = self._m(self.mm[(x >> 3) & 255] + self.aa + self.bb)
            self.mm[i] = y
            self.bb = self._m(self.mm[(y >> 11) & 255] + x)
            self.randrsl[i] = self.bb

    def _get_next(self):
        if self.randcnt == 0:
            self._isaac64()
            self.randcnt = 256
        self.randcnt -= 1
        return self.randrsl[self.randcnt]

    def generate_keystream_be(self, size):
        buf = bytearray(size)
        fb = size // 8
        for i in range(fb):
            k = self._get_next()
            struct.pack_into(">Q", buf, i * 8, k)
        rem = size % 8
        if rem > 0:
            lk = self._get_next()
            tmp = struct.pack(">Q", lk)
            buf[fb*8:fb*8+rem] = tmp[:rem]
        return bytes(buf)


def isaac64_decrypt(data, key):
    prng = Isaac64(key)
    ks = prng.generate_keystream_be(len(data))
    return bytes(a ^ b for a, b in zip(data, ks))


def fix_sns_url(url):
    import re
    if not url: return url
    url = url.replace("http://", "https://")
    url = re.sub(r"/\d+$", "/0", url)
    return url


class WcdbSnsReader:
    """朋友圈读取器 — 委托 WcdbNativeClient 查询 sns.db"""

    def __init__(self, client):
        """
        Args:
            client: WcdbNativeClient 实例（已完成 init + open）
        """
        self._client = client

    def get_timeline(self, limit=20, offset=0, usernames=None, keyword="", start_time=0, end_time=0):
        """Get SNS timeline. Delegates to WcdbNativeClient.get_sns_timeline()."""
        return self._client.get_sns_timeline(
            limit=limit, offset=offset, usernames=usernames,
            keyword=keyword, start_time=start_time, end_time=end_time,
        )

    def get_usernames(self):
        """Get all usernames who have posted Moments."""
        return self._client.get_sns_usernames()

    def _exec_query(self, sql, db_kind="message", db_path=""):
        """Execute SQL query via WcdbNativeClient.exec_query()."""
        return self._client.exec_query(kind=db_kind, db_path=db_path, sql=sql)

    @staticmethod
    def format_post(post):
        ts = post.get("createTime", 0)
        dt = datetime.fromtimestamp(ts) if ts else None
        nick = post.get("nickname", "?")
        content = post.get("contentDesc", "")[:120]
        lines = [f"--- {nick} ---"]
        if dt: lines.append(f"  时间: {dt.isoformat()}")
        if content: lines.append(f"  内容: {content}")
        media = post.get("media", [])
        if media:
            lines.append(f"  媒体: {len(media)} 个")
            for i, m in enumerate(media[:3]):
                lines.append(f"    [{i}] key={m.get('key', 0)}")
        return "\n".join(lines)


def _find_sns_db(wxid_data_dir):
    wxid_dirs = []
    for d in Path(wxid_data_dir).iterdir():
        if d.is_dir() and d.name.startswith("wxid_"):
            sdb = d / "db_storage" / "session" / "session.db"
            if sdb.exists(): wxid_dirs.append((sdb.stat().st_mtime, d))
    wxid_dirs.sort(key=lambda x: x[0], reverse=True)
    for _, wxid_dir in wxid_dirs:
        candidate = wxid_dir / "db_storage" / "sns" / "sns.db"
        if candidate.exists(): return str(candidate)
    return None


def main():
    from src.wechat.wcdb_client import WcdbNativeClient

    client = WcdbNativeClient()
    print("SNS Reader")
    print("=" * 60)

    print("\n[1/5] Init DLL...")
    client.init()
    print("  OK")

    print("\n[2/5] Open account...")
    client.open()
    print(f"  OK handle={client._handle}")

    reader = WcdbSnsReader(client)

    print("\n[3/5] Get SNS usernames...")
    try:
        usernames = reader.get_usernames()
        print(f"  OK {len(usernames)} publishers")
        if usernames: print(f"  First 10: {usernames[:10]}")
    except Exception as e:
        print(f"  WARN: {e}")
        usernames = []

    print("\n[4/5] Read SNS timeline...")
    try:
        posts = reader.get_timeline(limit=5, offset=0)
        print(f"  OK {len(posts)} posts")
        if posts:
            for post in posts:
                print(reader.format_post(post))
                print()
            total_media = sum(len(p.get("media", [])) for p in posts)
            print(f"  Stats: {len(posts)} posts, {total_media} media")
        else:
            print("  No posts found")
    except Exception as e:
        print(f"  Read failed: {e}")
        posts = []

    print("\n[5/5] ISAAC64 verify...")
    try:
        test_prng = Isaac64(0xDEADBEEF)
        test_ks = test_prng.generate_keystream_be(16)
        print(f"  ISAAC64 verify (seed=0xDEADBEEF): {test_ks.hex()}")
    except Exception as e:
        print(f"  Test failed: {e}")

    client.close()
    print("\nConnection closed")


if __name__ == "__main__":
    import sys, io
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
