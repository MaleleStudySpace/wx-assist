"""
微信收藏读取工具 —— 基于 WcdbNativeClient 的 wcdb_exec_query 接口

重构：不再独立加载 DLL / 做 DRM patch，改为接受一个 WcdbNativeClient 实例，
通过 client.exec_query() 查询 favorite.db。
"""
import json
import re
from pathlib import Path
from datetime import datetime
from xml.etree import ElementTree as ET


# ── 收藏类型映射 ──
FAV_TYPES = {
    1: "文本",
    2: "图片",
    3: "语音",
    4: "视频",
    5: "网页链接",
    6: "音乐",
    8: "文件",
    14: "笔记",
    18: "位置",
}


class WcdbFavReader:
    """读取微信收藏数据库（favorite.db），通过 WcdbNativeClient 委托查询"""

    def __init__(self, client):
        """
        Args:
            client: WcdbNativeClient 实例（已完成 init + open）
        """
        self._client = client
        self._favorite_db = client.favorite_db_path

    # ── 查询收藏 ──────────────────────────────────────────────────

    def _exec(self, sql: str) -> list[dict]:
        """在 favorite.db 上执行 SQL（用 kind='message'）"""
        return self._client.exec_query(kind="message", db_path=self._favorite_db, sql=sql)

    def count(self) -> int:
        r = self._exec("SELECT COUNT(*) as cnt FROM fav_db_item")
        return int(r[0]["cnt"]) if r else 0

    def type_distribution(self) -> dict[str, int]:
        r = self._exec(
            "SELECT type, COUNT(*) as cnt FROM fav_db_item "
            "GROUP BY type ORDER BY cnt DESC"
        )
        result = {}
        for row in (r or []):
            t = int(row["type"])
            name = FAV_TYPES.get(t, f"其他({t})")
            result[name] = int(row["cnt"])
        return result

    def get_items(self, limit=20, offset=0, fav_type=None) -> list[dict]:
        """获取收藏列表，返回解析后的结构化数据"""
        where = f"WHERE type={fav_type}" if fav_type else ""
        sql = (
            f"SELECT local_id, type, update_time, fromusr, content "
            f"FROM fav_db_item {where} "
            f"ORDER BY update_time DESC "
            f"LIMIT {limit} OFFSET {offset}"
        )
        rows = self._exec(sql) or []
        items = []
        for row in rows:
            item = self._parse_fav_row(row)
            items.append(item)
        return items

    def get_by_id(self, local_id: int) -> dict | None:
        sql = f"SELECT local_id, type, update_time, fromusr, content FROM fav_db_item WHERE local_id={local_id}"
        rows = self._exec(sql) or []
        if not rows:
            return None
        return self._parse_fav_row(rows[0])

    def _parse_fav_row(self, row: dict) -> dict:
        """将原始行数据解析为结构化字典"""
        ftype = int(row.get("type", 0))
        ts = int(float(row.get("update_time", 0)))
        dt = datetime.fromtimestamp(ts) if ts else None
        content = row.get("content", "")

        item = {
            "local_id": row["local_id"],
            "type": ftype,
            "type_name": FAV_TYPES.get(ftype, f"未知({ftype})"),
            "update_time": ts,
            "datetime": dt.isoformat() if dt else None,
            "from_user": row.get("fromusr", ""),
            "content_raw": content,
        }

        # 解析 XML 提取关键字段
        if content and content.strip().startswith("<favitem"):
            item.update(self._parse_fav_xml(content))

        return item

    def _parse_fav_xml(self, xml_str: str) -> dict:
        """解析 <favitem> XML 提取标题/描述/链接/来源等"""
        result = {}
        try:
            # 只处理 &#x0A; 换行符，不处理 &amp; 否则会导致URL中的 & 无法解析
            # 同时转义裸 & 为 &amp; 以修复微信XML中不规范的问题
            clean = xml_str.replace("&#x0A;", "\n")
            # 转义非实体引用的 &
            clean = re.sub(r'&(?!(?:amp|lt|gt|apos|quot|#x[0-9a-fA-F]+|#\d+);)', '&amp;', clean)
            root = ET.fromstring(clean)
        except Exception:
            return result

        # 标题 & 描述
        title_el = root.find("title")
        if title_el is not None and title_el.text:
            result["title"] = title_el.text

        desc_el = root.find("desc")
        if desc_el is not None and desc_el.text:
            result["description"] = desc_el.text.strip()

        # 来源信息
        source = root.find("source")
        if source is not None:
            result["source_type"] = source.get("sourcetype", "")
            result["source_id"] = source.get("sourceid", "")

            fromusr = source.find("fromusr")
            if fromusr is not None and fromusr.text:
                result["source_from"] = fromusr.text

            createtime = source.find("createtime")
            if createtime is not None and createtime.text:
                ct_ts = int(createtime.text)
                result["source_create_time"] = ct_ts
                result["source_datetime"] = (
                    datetime.fromtimestamp(ct_ts).isoformat()
                )

            msgid = source.find("msgid")
            if msgid is not None and msgid.text:
                result["msg_id"] = msgid.text

            link = source.find("link")
            if link is not None and link.text:
                result["link"] = link.text

        # 网页链接类型
        weburl = root.find("weburlitem")
        if weburl is not None:
            page_title = weburl.find("pagetitle")
            if page_title is not None and page_title.text:
                result["title"] = result.get("title") or page_title.text
            page_desc = weburl.find("pagedesc")
            if page_desc is not None and page_desc.text:
                result["description"] = result.get("description") or page_desc.text
            clean_url = weburl.find("clean_url")
            if clean_url is not None and clean_url.text:
                result["link"] = result.get("link") or clean_url.text

        # 聊天记录中的 dataitem (type 14 的 datalist 包含多个消息)
        datalist = root.find("datalist")
        if datalist is not None:
            data_items = []
            for di in datalist.findall("dataitem"):
                dtype = di.get("datatype", "")
                dataid = di.get("dataid", "")
                item_info = {
                    "type": dtype,
                    "src_name": self._get_text(di, "datasrcname"),
                    "desc": self._get_text(di, "datadesc"),
                    "time": self._get_text(di, "datasrctime"),
                    "head_url": self._get_text(di, "sourceheadurl"),
                }
                if dataid:
                    item_info["dataid"] = dataid
                # 如果是图片 (datatype=2)，提取 CDN 信息用于解密
                if dtype == "2":
                    cdn_dataurl = self._get_text(di, "cdn_dataurl")
                    cdn_datakey = self._get_text(di, "cdn_datakey")
                    fullmd5 = self._get_text(di, "fullmd5")
                    fullsize = self._get_text(di, "fullsize")
                    if cdn_dataurl and cdn_datakey:
                        item_info["cdn_dataurl"] = cdn_dataurl
                        item_info["cdn_datakey"] = cdn_datakey
                    if fullmd5:
                        item_info["fullmd5"] = fullmd5
                    if fullsize:
                        item_info["fullsize"] = int(fullsize)
                # 如果是文件 (datatype=8)，提取文件名等信息
                elif dtype == "8":
                    datatitle = self._get_text(di, "datatitle")
                    datafmt = self._get_text(di, "datafmt")
                    if datatitle:
                        item_info["file_name"] = datatitle
                    if datafmt:
                        item_info["file_type"] = datafmt
                data_items.append({k: v for k, v in item_info.items() if v})
            if data_items:
                result["chat_records"] = data_items

        # 图片的 CDN 信息（收集所有图片，包括顶层和聊天记录中的）
        image_list = []
        for di in (datalist.findall("dataitem") if datalist is not None else []):
            cdn_dataurl = self._get_text(di, "cdn_dataurl")
            cdn_datakey = self._get_text(di, "cdn_datakey")
            fullmd5 = self._get_text(di, "fullmd5")
            fullsize = self._get_text(di, "fullsize")
            dtype = di.get("datatype", "")
            if cdn_dataurl and cdn_datakey:
                img_info = {"dataurl": cdn_dataurl, "datakey": cdn_datakey}
                if fullmd5:
                    img_info["fullmd5"] = fullmd5
                if fullsize:
                    img_info["fullsize"] = int(fullsize)
                image_list.append(img_info)
            thumb_size = self._get_text(di, "thumbfullsize")
            if thumb_size and dtype == "2":
                result["image_size"] = int(thumb_size)
        if image_list:
            # 向后兼容: image_cdn 保留第一个（type=2 的图片优先）
            type2_imgs = [i for i, di in zip(image_list, datalist.findall("dataitem")) if di.get("datatype") == "2"]
            result["image_cdn"] = (type2_imgs[0] if type2_imgs else image_list[0])
            result["image_list"] = image_list

        return result

    @staticmethod
    def _get_text(element, tag: str) -> str | None:
        el = element.find(tag)
        return el.text.strip() if el is not None and el.text else None

    # ── 标签查询 ──────────────────────────────────────────────────

    def get_tags(self) -> list[dict]:
        """获取所有标签定义。返回 [{"local_id": "1", "name": "这对吗", ...}, ...]"""
        return self._exec(
            "SELECT local_id, server_id, name, seq "
            "FROM fav_tag_db_item ORDER BY seq"
        ) or []

    def get_tag_bindings(self) -> list[dict]:
        """获取标签-收藏关联。返回 [{"tag_local_id": "1", "fav_local_id": "10"}, ...]"""
        return self._exec(
            "SELECT tag_local_id, fav_local_id "
            "FROM fav_bind_tag_db_item WHERE op_code=1"
        ) or []

    # ── 图片导出 ──────────────────────────────────────────────────

    def get_image_cache_dir(self) -> Path | None:
        """返回微信收藏图片的本地缓存目录（与当前打开的账号对应）"""
        # 从 _favorite_db 路径反推 wxid 目录
        # favorite_db 路径: <wxid_data_dir>/<wxid_xxx>/db_storage/favorite/favorite.db
        fav_path = Path(self._favorite_db)
        wxid_dir = fav_path.parent.parent.parent  # favorite -> db_storage -> wxid_dir
        fav_cache = wxid_dir / "business" / "favorite"
        if fav_cache.exists():
            return fav_cache
        return None

    def find_cached_images(self) -> list[dict]:
        """
        扫描本地收藏缓存目录，匹配数据库中的图片收藏记录。
        返回结构: [{fav_item, temp_path, data_path, mid_path, thumb_path}, ...]
        """
        cache_dir = self.get_image_cache_dir()
        if not cache_dir:
            return []

        # 收集缓存文件
        temp_files = {}
        temp_dir = cache_dir / "temp"
        if temp_dir.exists():
            for f in temp_dir.iterdir():
                if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
                    temp_files[f.stem] = f

        data_files = {}
        data_dir = cache_dir / "data"
        if data_dir.exists():
            for f in data_dir.iterdir():
                if f.stat().st_size > 0:
                    data_files[f.name] = f

        mid_files = {}
        mid_dir = cache_dir / "mid"
        if mid_dir.exists():
            for f in mid_dir.iterdir():
                if f.stat().st_size > 0:
                    mid_files[f.name] = f

        thumb_files = {}
        thumb_dir = cache_dir / "thumb"
        if thumb_dir.exists():
            for f in thumb_dir.iterdir():
                if f.stat().st_size > 0:
                    thumb_files[f.name] = f

        # 查所有图片收藏
        items = self.get_items(limit=9999, fav_type=2)

        results = []
        for item in items:
            # 收集所有可能的时间戳: update_time + XML 中的 source_create_time
            timestamps = set()
            ts = item.get("update_time", 0)
            if ts:
                timestamps.add(datetime.fromtimestamp(ts).strftime("%Y%m%d%H%M%S"))

            # 从 content_raw 提取 source createtime
            content = item.get("content_raw", "")
            ct_match = re.search(r"<createtime>(\d+)</createtime>", content)
            if ct_match:
                ct_ts = int(ct_match.group(1))
                timestamps.add(datetime.fromtimestamp(ct_ts).strftime("%Y%m%d%H%M%S"))

            result = {"fav_item": item}

            # 策略1: 按时间戳匹配 temp 文件（收集所有匹配项，按大小排序）
            matched_temps = []
            for stem, fpath in temp_files.items():
                for dt_str in timestamps:
                    if dt_str in stem:
                        matched_temps.append(fpath)
                        break
            if matched_temps:
                # 按文件大小降序排列，大的在前（原图）
                matched_temps.sort(key=lambda f: f.stat().st_size, reverse=True)
                result["temp_path"] = matched_temps[0]  # 最大的作为主 temp
                result["_all_temp_paths"] = matched_temps  # 内部使用

            # 策略2: 用 dataitem 的 dataid 匹配哈希文件
            dataid_match = re.search(r'dataid="([a-f0-9]{32,})"', content)
            if dataid_match:
                dataid = dataid_match.group(1)
                if dataid in data_files:
                    result["data_path"] = data_files[dataid]
                if dataid in mid_files:
                    result["mid_path"] = mid_files[dataid]
                if dataid in thumb_files:
                    result["thumb_path"] = thumb_files[dataid]

            # 策略3: dataid 匹配失败 → 用文件大小相似度匹配 data/ 中的文件
            if not result.get("data_path") and matched_temps:
                # 用最大的 temp 文件大小来匹配 data/ 中相似大小的原图
                best_temp = matched_temps[0]
                temp_size = best_temp.stat().st_size
                best_match = None
                best_diff = float("inf")
                for h, fpath in data_files.items():
                    diff = abs(fpath.stat().st_size - temp_size)
                    if diff < temp_size * 0.05:  # 5% 容差
                        if diff < best_diff:
                            best_diff = diff
                            best_match = fpath
                if best_match:
                    result["data_path"] = best_match

            results.append(result)

        return results

    def export_images(self, output_dir: str, prefer="data", all_sizes: bool = False) -> list[str]:
        """
        导出所有收藏图片到指定目录。

        Args:
            output_dir: 目标目录路径
            prefer: 优先使用的缓存层 — "data"(原图哈希), "mid"(中图), "temp"(临时文件), "thumb"(缩略图)
            all_sizes: True=导出所有可用尺寸（原图/中图/缩略图），False=只导出最佳质量的一张

        Returns:
            成功导出的文件路径列表
        """
        dest = Path(output_dir)
        dest.mkdir(parents=True, exist_ok=True)

        cached = self.find_cached_images()
        exported = []

        for entry in cached:
            item = entry["fav_item"]
            lid = item["local_id"]
            ts = item.get("update_time", 0)
            dt_str = datetime.fromtimestamp(ts).strftime("%Y%m%d_%H%M%S") if ts else str(lid)

            # 收集所有可用的源文件
            all_sources: list[tuple[str, Path]] = []

            # data/ 哈希文件
            dp = entry.get("data_path")
            if dp and dp.exists():
                all_sources.append(("data", dp))

            # mid/ 哈希文件
            mp = entry.get("mid_path")
            if mp and mp.exists():
                all_sources.append(("mid", mp))

            # temp/ 文件（可能有多个尺寸）
            temps = entry.get("_all_temp_paths", [])
            for tp in temps:
                all_sources.append(("temp", tp))

            # thumb/ 哈希文件
            tp_thumb = entry.get("thumb_path")
            if tp_thumb and tp_thumb.exists():
                all_sources.append(("thumb", tp_thumb))

            if not all_sources:
                continue

            # 去重（同一文件可能被多次匹配）
            seen = set()
            unique_sources: list[tuple[str, Path]] = []
            for layer, p in all_sources:
                rp = p.resolve()
                if rp not in seen:
                    seen.add(rp)
                    unique_sources.append((layer, p))

            sources_to_export: list[tuple[str, Path]] = []
            if all_sizes:
                # 导出所有尺寸
                sources_to_export = unique_sources
            else:
                # 只导出最佳质量
                for layer in [prefer, "data", "mid", "temp", "thumb"]:
                    for lyr, p in unique_sources:
                        if lyr == layer:
                            sources_to_export.append((lyr, p))
                            break
                    if sources_to_export:
                        break

            for layer, src in sources_to_export:
                # 测试可读性
                try:
                    src.read_bytes()[:1]
                except PermissionError:
                    continue

                ext = src.suffix or ".jpg"

                # 生成输出文件名（all_sizes 时加尺寸标签）
                if all_sizes:
                    size_kb = src.stat().st_size / 1024
                    if size_kb > 100:
                        label = "original"
                    elif size_kb > 40:
                        label = "medium"
                    else:
                        label = "thumb"
                else:
                    label = None

                title = item.get("title", "")
                if title:
                    safe_title = re.sub(r'[\\/:*?"<>|]', "_", title)[:50]
                    base_name = f"{dt_str}_{safe_title}"
                else:
                    base_name = f"{dt_str}_fav_{lid}"

                if label:
                    out_name = f"{base_name}_{label}{ext}"
                else:
                    out_name = f"{base_name}{ext}"

                out_path = dest / out_name
                counter = 1
                base_no_ext = out_name[:-len(ext)] if out_name.endswith(ext) else out_name
                while out_path.exists():
                    out_path = dest / f"{base_no_ext}_{counter}{ext}"
                    counter += 1

                try:
                    out_path.write_bytes(src.read_bytes())
                    exported.append(str(out_path))
                except PermissionError:
                    continue

        return exported


# ── 测试入口 ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys, io
    # 解决 Windows 控制台 GBK 编码问题
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    from src.wechat.wcdb_client import WcdbNativeClient

    client = WcdbNativeClient()
    print("初始化 DLL...")
    client.init()
    print("打开数据库...")
    client.open()

    reader = WcdbFavReader(client)

    print(f"\n收藏总数: {reader.count()}")
    print(f"\n类型分布:")
    for name, cnt in reader.type_distribution().items():
        print(f"  {name}: {cnt}")

    print("\n" + "=" * 70)
    print("全部收藏:")
    items = reader.get_items(limit=20)
    for i, item in enumerate(items):
        print(f"\n[{i + 1}] ID={item['local_id']} | {item['type_name']} | {item.get('datetime', '')}")
        if item.get("title"):
            print(f"    标题: {item['title']}")
        if item.get("description"):
            print(f"    描述: {item['description'][:120]}")
        if item.get("link"):
            print(f"    链接: {item['link'][:200]}")
        if item.get("data_items"):
            print(f"    数据项: {len(item['data_items'])} 条")
            for di in item["data_items"][:5]:
                if di.get("cdn_datakey"):
                    print(f"      [图片] key={di['cdn_datakey']}")
                elif di.get("src_name"):
                    print(f"      [{di.get('time','')}] {di.get('src_name','?')}: {di.get('desc','')[:50]}")

    # ── 图片导出测试 ──────────────────────────────────────────
    print("\n" + "=" * 70)
    print("[Image Export Test]")

    cache_dir = reader.get_image_cache_dir()
    print(f"  Cache dir: {cache_dir}")

    cached = reader.find_cached_images()
    print(f"  Matched: {len(cached)} cached image(s)")
    for entry in cached:
        item = entry["fav_item"]
        print(f"\n  [ID={item['local_id']}] {item.get('datetime', 'N/A')}")
        temps = entry.get("_all_temp_paths", [])
        for tp in temps:
            print(f"    temp: {tp.name} ({tp.stat().st_size/1024:.1f} KB)")
        for k in ("data_path", "mid_path", "thumb_path"):
            v = entry.get(k)
            if v:
                print(f"    {k.replace('_path','')}: {v.name}")

    if cached:
        # 测试单图导出（最佳质量）
        export_dir = Path(__file__).resolve().parent / "exported_favorites"
        exported = reader.export_images(str(export_dir), prefer="temp", all_sizes=False)
        print(f"\n  [Best quality] -> {export_dir}")
        for p in exported:
            print(f"    OK {Path(p).name} ({Path(p).stat().st_size/1024:.1f} KB)")

        # 测试全尺寸导出
        export_all_dir = Path(__file__).resolve().parent / "exported_favorites_all"
        exported_all = reader.export_images(str(export_all_dir), prefer="temp", all_sizes=True)
        print(f"\n  [All sizes] -> {export_all_dir}")
        for p in exported_all:
            print(f"    OK {Path(p).name} ({Path(p).stat().st_size/1024:.1f} KB)")

    client.close()
    print("\nDone.")
