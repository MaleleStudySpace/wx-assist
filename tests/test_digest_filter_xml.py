"""filter_messages 对伪装成文本的 appmsg XML 的清洗。

WCDB 把群接龙 / 引用回复 / 名片 / 聊天记录存成 msg_type=1，content 是完整
appmsg XML。MEDIA_RAW_TYPES 只覆盖 {3,34,43,47,49}，所以这些 XML 原样进了
LLM prompt —— 实测单条最长 17,015 字符，6 群 24h 打包 694,800 字符，
清洗后 321,668（-54%），且信息量没有损失。
"""
import unittest

from src.assistant.digest import (
    MEDIA_PLACEHOLDERS,
    XML_MIN_LEN,
    _clean_xml_content,
    filter_messages,
)


def appmsg(title, extra_len=0, body=""):
    """构造一段贴近真实结构的 appmsg XML。"""
    pad = ("x" * extra_len) if extra_len else ""
    return (
        '<?xml version="1.0"?>\n<msg>\n\t<appmsg appid="" sdkver="0">\n'
        f"\t\t<title>{title}</title>\n\t\t<des />\n\t\t<action />\n"
        "\t\t<type>57</type>\n\t\t<showtype>0</showtype>\n"
        "\t\t<appattach>\n\t\t\t<totallen>0</totallen>\n\t\t\t<attachid />"
        f"\n\t\t\t<fileext>html</fileext>\n\t\t\t<pad>{pad}</pad>\n"
        "\t\t</appattach>\n"
        f"{body}"
        "\t</appmsg>\n\t<fromusername>wxid_test</fromusername>\n\t<scene>0</scene>\n</msg>"
    )


def refermsg(outer_title, inner_content):
    """引用回复：外层 title 是回复正文，<refermsg> 内层 content 是被引用文本。"""
    return appmsg(
        outer_title,
        body=(
            "\t\t<refermsg>\n\t\t\t<type>1</type>\n\t\t\t<svrid>777</svrid>\n"
            "\t\t\t<fromusr>wxid_abc</fromusr>\n\t\t\t<displayname>张三</displayname>\n"
            f"\t\t\t<content>{inner_content}</content>\n\t\t</refermsg>\n"
        ),
    )


class TestCleanXmlContent(unittest.TestCase):

    def test_appmsg_title_extracted(self):
        self.assertEqual(_clean_xml_content(appmsg("本周接龙")), "{{app: 本周接龙}}")

    def test_huge_xml_collapses_to_title(self):
        """实测单条最长 17,015 字符 —— 必须被压成几十字。"""
        xml = appmsg("本周接龙", extra_len=17000)
        self.assertGreater(len(xml), 17000)
        cleaned = _clean_xml_content(xml)
        self.assertEqual(cleaned, "{{app: 本周接龙}}")
        self.assertLess(len(cleaned), 30)

    def test_cdata_title_unwrapped(self):
        xml = appmsg("<![CDATA[标题在CDATA里]]>")
        self.assertEqual(_clean_xml_content(xml), "{{app: 标题在CDATA里}}")

    def test_refermsg_outer_title_wins(self):
        """取外层 title（回复正文），而不是 <refermsg> 里的被引用文本。"""
        xml = refermsg("我同意这个方案", "被引用的原始消息文本")
        self.assertEqual(_clean_xml_content(xml), "{{app: 我同意这个方案}}")

    def test_xml_without_title_falls_back(self):
        xml = ('<?xml version="1.0"?>\n<msg>\n\t<appmsg appid="">\n'
               "\t\t<type>33</type>\n\t\t<des>没有标题字段</des>\n"
               "\t</appmsg>\n</msg>")
        self.assertEqual(_clean_xml_content(xml), "{{app_message}}")

    def test_empty_title_falls_back(self):
        self.assertEqual(_clean_xml_content(appmsg("   ")), "{{app_message}}")

    def test_title_whitespace_collapsed_and_capped(self):
        cleaned = _clean_xml_content(appmsg("  多行\n\t标题  内容  ", extra_len=100))
        self.assertEqual(cleaned, "{{app: 多行 标题 内容}}")
        long_cleaned = _clean_xml_content(appmsg("标" * 300))
        self.assertEqual(long_cleaned, "{{app: " + "标" * 120 + "}}")

    def test_plain_text_returns_none(self):
        """非 XML 一律返回 None，表示"别动"。"""
        self.assertIsNone(_clean_xml_content("这是一条普通的中文聊天消息，长度超过六十个字符所以能过长度门槛，但它不是 XML 所以必须原样保留"))
        self.assertIsNone(_clean_xml_content(""))
        self.assertIsNone(_clean_xml_content(None))

    def test_short_text_with_angle_bracket_untouched(self):
        """长度门槛防误伤 "<3 你" 这类短文本。"""
        for text in ("<3 你", "a<b", "1<2 且 3>2", "<好>"):
            self.assertIsNone(_clean_xml_content(text), text)
        self.assertLess(len("<3 你"), XML_MIN_LEN)

    def test_long_text_starting_with_angle_bracket_but_no_close_untouched(self):
        text = "<重要通知>" + "请大家注意今天的安排" * 10
        self.assertIsNone(_clean_xml_content(text))

    def test_sysmsg_shape_recognised(self):
        xml = '<?xml version="1.0"?>\n<sysmsg>\n\t<title>系统提示标题</title>\n' + ("y" * 80) + "\n</sysmsg>"
        self.assertEqual(_clean_xml_content(xml), "{{app: 系统提示标题}}")


def img_xml(pad=60):
    return ('<?xml version="1.0"?>\n<msg>\n\t<img aeskey="a2506998" encryver="1" '
            'cdnthumburl="' + "y" * pad + '" />\n</msg>')


def emoji_xml():
    return ('<msg><emoji fromusername = "wxid_a" tousername = "1@chatroom" type="2" '
            'idbuffer="media:0_0" md5="e083e2f4" len = "303186" productid="" /></msg>')


def voicemsg_xml():
    return ('<?xml version="1.0"?>\n<msg>\n\t<voicemsg aeskey="a250" voicelength="2000" '
            'voiceformat="1" endflag="1" length="1024" /></msg>')


def videomsg_xml():
    return ('<?xml version="1.0"?>\n<msg>\n\t<videomsg aeskey="a9e3" cdnvideourl="'
            + "z" * 60 + '" /></msg>')


def location_xml():
    return ('<?xml version="1.0"?>\n<msg>\n\t<location x="31.23" y="121.47" scale="16" '
            'label="上海市黄浦区" poiname="" /></msg>')


def contact_card_xml():
    return ('<?xml version="1.0"?>\n<msg bigheadimgurl="http://wx.qlogo.cn/mmhead/'
            + "a" * 60 + '" smallheadimgurl="http://x" username="wxid_abc" '
            'nickname="张三" antispamticket="v2_abc" /></msg>')


def pushmail_xml():
    return ('<msg><pushmail><content><subject><![CDATA[成功登入网上交易平台 ]]></subject>'
            "<attach>false</attach><sender><![CDATA[EDDID]]></sender>"
            "<digest><![CDATA[阁下已于 2026-09-01 10:34:23 登入交易账户 58***81210]]></digest>"
            "<date>2026-09-01 10:34:27</date></content></pushmail></msg>")


class TestCleanXmlMediaKinds(unittest.TestCase):
    """无 <title> 的 XML 必须按结构给准确标签 —— 实测 72h 内有 1353 条。"""

    def test_img_maps_to_image_placeholder(self):
        self.assertEqual(_clean_xml_content(img_xml()), "{{ image }}")

    def test_emoji_maps_to_sticker_placeholder(self):
        self.assertEqual(_clean_xml_content(emoji_xml()), "{{ sticker }}")

    def test_voicemsg_maps_to_voice_placeholder(self):
        self.assertEqual(_clean_xml_content(voicemsg_xml()), "{{ voice }}")

    def test_videomsg_maps_to_video_placeholder(self):
        self.assertEqual(_clean_xml_content(videomsg_xml()), "{{ video }}")

    def test_location_maps_to_location_placeholder(self):
        self.assertEqual(_clean_xml_content(location_xml()), "{{ location }}")

    def test_contact_card_maps_to_contact_card_placeholder(self):
        self.assertEqual(_clean_xml_content(contact_card_xml()), "{{ contact_card }}")

    def test_media_labels_reuse_msg_type_vocabulary(self):
        """XML 推出的标签必须与 MEDIA_PLACEHOLDERS 一致，否则 LLM 会看到两套词汇。"""
        self.assertEqual(_clean_xml_content(img_xml()), MEDIA_PLACEHOLDERS[3])
        self.assertEqual(_clean_xml_content(emoji_xml()), MEDIA_PLACEHOLDERS[47])
        self.assertEqual(_clean_xml_content(voicemsg_xml()), MEDIA_PLACEHOLDERS[34])
        self.assertEqual(_clean_xml_content(videomsg_xml()), MEDIA_PLACEHOLDERS[43])

    def test_pushmail_subject_extracted_not_discarded(self):
        """27 条 pushmail 带真实文本，不能被压成无信息的占位符。"""
        self.assertEqual(_clean_xml_content(pushmail_xml()),
                         "{{mail: 成功登入网上交易平台}}")

    def test_titled_appmsg_wins_over_media_marker(self):
        xml = appmsg("含图卡片", body='\t\t<img src="x" />\n')
        self.assertEqual(_clean_xml_content(xml), "{{app: 含图卡片}}")

    def test_media_marker_ignored_outside_opening_region(self):
        """正文深处偶然出现 <img 不应误判成图片消息。"""
        xml = ("<?xml version=\"1.0\"?>\n<msg>\n\t<appmsg appid=\"\">\n"
               "\t\t<des>" + "d" * 500 + "</des>\n\t\t<img src=\"late\" />\n"
               "\t</appmsg>\n</msg>")
        self.assertEqual(_clean_xml_content(xml), "{{app_message}}")

    def test_filter_messages_maps_img_xml_at_msg_type_1(self):
        """核心场景：msg_type=1 的图片 XML 必须得到 {{ image }}，不是 {{app_message}}。"""
        msgs = [{"content": img_xml(pad=1200), "msg_type": 1, "sender_name": "A"}]
        self.assertEqual(filter_messages(msgs)[0]["content"], "{{ image }}")


class TestFilterMessagesXml(unittest.TestCase):

    def test_msg_type_1_xml_is_cleaned(self):
        """核心场景：msg_type=1 但 content 是 appmsg XML。"""
        msgs = [{"content": appmsg("本周接龙", extra_len=5000), "msg_type": 1,
                 "sender_name": "A"}]
        result = filter_messages(msgs)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "{{app: 本周接龙}}")

    def test_msg_type_49_with_title_gets_title(self):
        msgs = [{"content": appmsg("小程序分享"), "msg_type": 49, "sender_name": "A"}]
        result = filter_messages(msgs)
        self.assertEqual(result[0]["content"], "{{app: 小程序分享}}")

    def test_msg_type_49_without_xml_keeps_app_placeholder(self):
        msgs = [{"content": "不是XML的普通内容" * 20, "msg_type": 49, "sender_name": "A"}]
        result = filter_messages(msgs)
        self.assertEqual(result[0]["content"], "{{ app_message }}")

    def test_pure_media_placeholders_unchanged(self):
        """3/34/43/47 的既有行为零回归。"""
        msgs = [
            {"content": "[图片]", "msg_type": 3},
            {"content": "[语音]", "msg_type": 34},
            {"content": "[视频]", "msg_type": 43},
            {"content": "[表情]", "msg_type": 47},
        ]
        result = filter_messages(msgs)
        self.assertEqual([m["content"] for m in result],
                         ["{{ image }}", "{{ voice }}", "{{ video }}", "{{ sticker }}"])

    def test_pure_media_with_xml_content_still_uses_fixed_placeholder(self):
        """图片消息即便 content 长得像 XML 也走固定占位符，不被 title 覆盖。"""
        msgs = [{"content": appmsg("图片描述"), "msg_type": 3}]
        self.assertEqual(filter_messages(msgs)[0]["content"], "{{ image }}")

    def test_encrypted_hex_wins_over_xml_branch(self):
        hex_content = "a1b2c3d4e5f6" * 12
        msgs = [{"content": hex_content, "msg_type": 1}]
        self.assertEqual(filter_messages(msgs)[0]["content"], "{{ encrypted }}")

    def test_normal_text_untouched(self):
        msgs = [{"content": "今天讨论了第三季度的预算安排，结论是先冻结招聘", "msg_type": 1}]
        self.assertEqual(filter_messages(msgs)[0]["content"],
                         "今天讨论了第三季度的预算安排，结论是先冻结招聘")

    def test_xml_cleaning_reduces_total_chars(self):
        """6 段大 XML → 清洗后总字符必须显著下降（实测方向 -54%，这里用宽松阈值）。"""
        msgs = [{"content": appmsg(f"接龙第{i}期", extra_len=17000), "msg_type": 1}
                for i in range(6)]
        before = sum(len(m["content"]) for m in msgs)
        result = filter_messages(msgs)
        after = sum(len(m["content"]) for m in result)
        self.assertGreater(before, 100000)
        self.assertLess(after, before * 0.2)

    def test_mixed_batch_keeps_order_and_count(self):
        msgs = [
            {"content": appmsg("接龙"), "msg_type": 1},
            {"content": "正常讨论内容，关于下周的排期", "msg_type": 1},
            {"content": "[图片]", "msg_type": 3},
            {"content": "收到", "msg_type": 1},          # NOISE_REPLIES → 丢弃
        ]
        result = filter_messages(msgs)
        self.assertEqual([m["content"] for m in result],
                         ["{{app: 接龙}}", "正常讨论内容，关于下周的排期", "{{ image }}"])


if __name__ == "__main__":
    unittest.main()
