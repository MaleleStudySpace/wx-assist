"""Unit tests for TaskCenter — task lifecycle tracking."""

import os
import tempfile
import unittest
from pathlib import Path

# Ensure project root is on path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestTaskCenter(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.tmp_dir) / "test_task_center.db"
        from src.assistant.task_center import TaskCenter
        self.tc = TaskCenter(db_path=self.db_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_create_task(self):
        tid = self.tc.create_task('group_digest', 'manual', 'xxx@chatroom', '测试群')
        self.assertIsNotNone(tid)
        self.assertGreater(tid, 0)

    def test_update_task(self):
        tid = self.tc.create_task('oa_digest', 'scheduler', 'grp_001', '科技')
        ok = self.tc.update_task(tid, status='running', progress='正在获取文章')
        self.assertTrue(ok)
        task = self.tc.get_task(tid)
        self.assertEqual(task['status'], 'running')
        self.assertEqual(task['progress'], '正在获取文章')
        self.assertIsNotNone(task['started_at'])

    def test_complete_task(self):
        tid = self.tc.create_task('group_digest', 'manual', 'xxx@chatroom', '测试群')
        self.tc.update_task(tid, status='running', progress='AI 生成摘要中')
        ok = self.tc.complete_task(tid, result='摘要生成完成', msg_count=42)
        self.assertTrue(ok)
        task = self.tc.get_task(tid)
        self.assertEqual(task['status'], 'completed')
        self.assertEqual(task['msg_count'], 42)
        self.assertIsNotNone(task['finished_at'])

    def test_fail_task(self):
        tid = self.tc.create_task('oa_digest', 'scheduler', 'grp_002', '港股')
        ok = self.tc.fail_task(tid, error='WCDB 不可用')
        self.assertTrue(ok)
        task = self.tc.get_task(tid)
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['error'], 'WCDB 不可用')
        self.assertIsNotNone(task['finished_at'])

    def test_list_tasks_all(self):
        self.tc.create_task('group_digest', 'manual', 'g1', '群1')
        self.tc.create_task('oa_digest', 'scheduler', 'g2', '群2')
        tasks = self.tc.list_tasks()
        self.assertEqual(len(tasks), 2)

    def test_list_tasks_filter_status(self):
        t1 = self.tc.create_task('group_digest', 'manual', 'g1', '群1')
        self.tc.create_task('oa_digest', 'scheduler', 'g2', '群2')
        self.tc.update_task(t1, status='running')
        tasks = self.tc.list_tasks(status='running')
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['task_type'], 'group_digest')

    def test_list_tasks_filter_type(self):
        self.tc.create_task('group_digest', 'manual', 'g1', '群1')
        self.tc.create_task('oa_digest', 'scheduler', 'g2', '群2')
        tasks = self.tc.list_tasks(task_type='oa_digest')
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['task_type'], 'oa_digest')

    def test_get_task_not_found(self):
        task = self.tc.get_task(99999)
        self.assertIsNone(task)

    def test_update_push_result(self):
        tid = self.tc.create_task('group_digest', 'manual', 'g1', '群1')
        ok = self.tc.update_push_result(tid, 'success')
        self.assertTrue(ok)
        task = self.tc.get_task(tid)
        self.assertEqual(task['push_status'], 'success')

    def test_cleanup_expired(self):
        tid = self.tc.create_task('group_digest', 'manual', 'g1', '群1')
        self.tc.complete_task(tid, result='done')
        # Manually set finished_at to 100 hours ago
        import sqlite3
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE task_center SET finished_at=datetime('now', '-100 hours') WHERE id=?",
                (tid,),
            )
            conn.commit()
        deleted = self.tc.cleanup_expired(max_age_hours=72)
        self.assertEqual(deleted, 1)
        # Verify it's gone
        task = self.tc.get_task(tid)
        self.assertIsNone(task)

    def test_cleanup_does_not_delete_running(self):
        tid = self.tc.create_task('group_digest', 'manual', 'g1', '群1')
        self.tc.update_task(tid, status='running')
        deleted = self.tc.cleanup_expired(max_age_hours=0)
        self.assertEqual(deleted, 0)
        task = self.tc.get_task(tid)
        self.assertIsNotNone(task)

    def test_stale_running_marked_failed_on_init(self):
        """Simulate bot restart: running tasks should be marked failed."""
        tid = self.tc.create_task('group_digest', 'scheduler', 'g1', '群1')
        self.tc.update_task(tid, status='running')
        # Re-create TaskCenter (simulates restart)
        from src.assistant.task_center import TaskCenter
        tc2 = TaskCenter(db_path=self.db_path)
        task = tc2.get_task(tid)
        self.assertEqual(task['status'], 'failed')
        self.assertIn('重启', task['error'])

    def test_count_running(self):
        t1 = self.tc.create_task('group_digest', 'manual', 'g1', '群1')
        t2 = self.tc.create_task('oa_digest', 'manual', 'g2', '群2')
        self.tc.update_task(t1, status='running')
        self.tc.update_task(t2, status='running')
        count = self.tc.count_running()
        self.assertEqual(count, 2)

    def test_complete_task_stores_full_result(self):
        """result 不再截断到 500 —— 重推功能需要完整摘要内容（上限 50000）。"""
        tid = self.tc.create_task('group_digest', 'manual', 'g1', '群1')
        long_result = 'x' * 1000
        self.tc.complete_task(tid, result=long_result)
        task = self.tc.get_task(tid)
        self.assertEqual(len(task['result']), 1000)  # 完整保存，不截断

    def test_complete_task_result_capped_at_50000(self):
        """超长输出（如 cron skill）仍受 50000 安全上限保护。"""
        tid = self.tc.create_task('cron', 'scheduler', 'job1', '任务1')
        huge = 'x' * 60000
        self.tc.complete_task(tid, result=huge)
        task = self.tc.get_task(tid)
        self.assertEqual(len(task['result']), 50000)

    def test_create_task_with_outbox_id(self):
        tid = self.tc.create_task('oa_article_alert', 'system', 'gh_1', '公众号 · 文章', outbox_id=42)
        self.assertIsNotNone(tid)
        task = self.tc.get_task(tid)
        self.assertEqual(task['outbox_id'], 42)

    def test_get_latest_completed_digest_exact_group_and_scheduled_only(self):
        """摘要查询只返回精确分组的定时任务，且不受推送状态影响。"""
        scheduled = self.tc.create_task('oa_digest', 'scheduler', 'oa-1', '开源项目')
        self.tc.complete_task(scheduled, result='完整公众号摘要' * 200, articles_count=3)
        self.tc.update_push_result(scheduled, 'failed', '推送失败')

        manual = self.tc.create_task('oa_digest', 'manual', 'oa-1', '开源项目')
        self.tc.complete_task(manual, result='手动生成的更新摘要')

        other = self.tc.create_task('oa_digest', 'scheduler', 'oa-2', '其他分组')
        self.tc.complete_task(other, result='不应返回')

        latest = self.tc.get_latest_completed_digest('oa_digest', '开源项目')
        self.assertIsNotNone(latest)
        self.assertEqual(latest['id'], scheduled)
        self.assertEqual(latest['source'], 'scheduler')
        self.assertEqual(latest['push_status'], 'failed')
        self.assertEqual(len(latest['result']), len('完整公众号摘要' * 200))

    def test_get_latest_completed_digest_supports_group_digest(self):
        task_id = self.tc.create_task(
            'group_digest', 'catchup', 'dg-1', '技术交流群'
        )
        self.tc.complete_task(task_id, result='群聊摘要正文', msg_count=12)

        latest = self.tc.get_latest_completed_digest(
            'group_digest', '技术交流群'
        )
        self.assertIsNotNone(latest)
        self.assertEqual(latest['id'], task_id)
        self.assertEqual(latest['msg_count'], 12)
        self.assertEqual(latest['result'], '群聊摘要正文')

    def test_get_latest_completed_digest_excludes_running_and_expired(self):
        running = self.tc.create_task('oa_digest', 'scheduler', 'oa-1', '开源项目')
        self.tc.update_task(running, status='running')
        self.assertIsNone(
            self.tc.get_latest_completed_digest('oa_digest', '开源项目')
        )

        completed = self.tc.create_task('oa_digest', 'scheduler', 'oa-1', '开源项目')
        self.tc.complete_task(completed, result='过期摘要')
        import sqlite3
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE task_center SET created_at=? WHERE id=?",
                ('2020-01-01T00:00:00', completed),
            )
            conn.commit()
        self.assertIsNone(
            self.tc.get_latest_completed_digest(
                'oa_digest', '开源项目', within_hours=48
            )
        )

    def test_get_latest_completed_digest_rejects_unknown_type_or_blank_name(self):
        self.assertIsNone(
            self.tc.get_latest_completed_digest('cron', '开源项目')
        )
        self.assertIsNone(
            self.tc.get_latest_completed_digest('oa_digest', '  ')
        )

    def test_get_failed_push_tasks(self):
        # 推送失败（digest 类）
        t1 = self.tc.create_task('oa_digest', 'scheduler', 'g1', '群1')
        self.tc.complete_task(t1, result='摘要')
        self.tc.update_push_result(t1, 'failed', 'timeout')
        # oa_article_alert 推送失败（fail_task 标记）
        t2 = self.tc.create_task('oa_article_alert', 'system', 'gh_2', '公众号 · 文章')
        self.tc.fail_task(t2, error='rate limited')
        # 成功推送的不应出现在列表
        t3 = self.tc.create_task('oa_digest', 'scheduler', 'g3', '群3')
        self.tc.complete_task(t3, result='摘要')
        self.tc.update_push_result(t3, 'success')
        tasks = self.tc.get_failed_push_tasks(hours=24)
        ids = {t['id'] for t in tasks}
        self.assertIn(t1, ids)
        self.assertIn(t2, ids)
        self.assertNotIn(t3, ids)

    def test_get_failed_push_tasks_excludes_partial_and_skipped(self):
        """部分成功是终态：已有渠道收到消息，重推会让这些渠道收到重复内容。"""
        t_partial = self.tc.create_task('group_digest', 'scheduler', 'g1', '群1')
        self.tc.complete_task(t_partial, result='摘要')
        self.tc.update_push_result(t_partial, 'partial', 'feishu: token expired')

        t_skipped = self.tc.create_task('cron', 'scheduler', 'g2', '定时任务')
        self.tc.complete_task(t_skipped, result='输出')
        self.tc.update_push_result(t_skipped, 'skipped', '未绑定任何推送渠道')

        t_failed = self.tc.create_task('group_digest', 'scheduler', 'g3', '群3')
        self.tc.complete_task(t_failed, result='摘要')
        self.tc.update_push_result(t_failed, 'failed', 'timeout')

        ids = {t['id'] for t in self.tc.get_failed_push_tasks(hours=24)}
        self.assertIn(t_failed, ids)
        self.assertNotIn(t_partial, ids)
        self.assertNotIn(t_skipped, ids)

    def test_partial_is_not_counted_as_failed(self):
        """部分成功不计入失败红点与失败筛选，只在推送状态位单独展示。"""
        t = self.tc.create_task('group_digest', 'scheduler', 'g1', '群1')
        self.tc.complete_task(t, result='摘要')
        self.tc.update_push_result(t, 'partial', 'feishu 发送失败')

        self.assertEqual(self.tc.count_failed_since(), 0)
        self.assertEqual(len(self.tc.list_tasks(status='failed')), 0)
        # 内容已生成，任务本身仍归在已完成
        self.assertEqual(len(self.tc.list_tasks(status='completed')), 1)

    def test_list_tasks_exclude(self):
        self.tc.create_task('cache_oa_incremental', 'system', 'g1', 'OA增量同步')
        self.tc.create_task('cache_fav_incremental', 'system', 'g2', '收藏增量')
        self.tc.create_task('group_digest', 'manual', 'g3', '群3')
        tasks = self.tc.list_tasks(exclude='cache_')
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['task_type'], 'group_digest')

    def test_count_failed_since_filters_correctly(self):
        """count_failed_since 应正确过滤早于 since 的任务 (含失败 status 和 push_status)。"""
        import time as time_mod
        old = self.tc.create_task('oa_digest', 'scheduler', 'g1', '群1')
        self.tc.complete_task(old, result='旧摘要')
        self.tc.update_push_result(old, 'failed', 'old')
        # 把 created_at 改到 1 小时前
        old_time = time_mod.strftime('%Y-%m-%dT%H:%M:%S',
                                     time_mod.localtime(time_mod.time() - 3600))
        import sqlite3
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("UPDATE task_center SET created_at=? WHERE id=?",
                         (old_time, old))
            conn.commit()
        # since 用 5 秒前 (前端 ISO 格式) — 留余量避免同秒边界
        import datetime as dt
        since_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5)
        since_iso = since_dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')
        # 旧任务 (1h 前) 应被过滤
        self.assertEqual(self.tc.count_failed_since(since_iso), 0)
        # 没传 since 应返回全部
        self.assertEqual(self.tc.count_failed_since(''), 1)
        # 新失败任务应被计入 (创建于 since 之后)
        new = self.tc.create_task('oa_digest', 'scheduler', 'g2', '群2')
        self.tc.complete_task(new, result='新摘要')
        self.tc.update_push_result(new, 'failed', 'new')
        self.assertEqual(self.tc.count_failed_since(since_iso), 1)

    def test_count_failed_since_handles_malformed_since(self):
        """since 解析失败时 fallback 到原字符串, 不抛异常。"""
        self.tc.create_task('oa_digest', 'scheduler', 'g1', '群1')
        self.tc.complete_task(self.tc.create_task('oa_digest', 'scheduler', 'g2', 'g2'), result='x')
        self.tc.update_push_result(self.tc.list_tasks()[0]['id'], 'failed', 'err')
        # 无法解析的字符串: 走 fallback (原字符串比较), 不抛异常即可
        try:
            n = self.tc.count_failed_since('not-a-date')
            self.assertIsInstance(n, int)
        except Exception as e:
            self.fail(f'count_failed_since 不应抛异常: {e}')

    def test_iso_to_local_str(self):
        """_iso_to_local_str: UTC ISO 转本地时间字符串, 与 _now() 同格式。"""
        from src.assistant.task_center import _iso_to_local_str
        import datetime as dt
        # 已知 UTC 2026-08-09T13:00:00Z 在 UTC+8 应是 21:00:00
        utc = dt.datetime(2026, 8, 9, 13, 0, 0, tzinfo=dt.timezone.utc)
        local_str = _iso_to_local_str(utc.isoformat().replace('+00:00', 'Z'))
        # 不强求具体时区 (CI/测试机可能不是 UTC+8), 只验证格式 & 解析正确
        self.assertRegex(local_str, r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$')
        parsed_back = dt.datetime.fromisoformat(local_str)
        self.assertEqual(parsed_back.year, 2026)
        # 跨时区再转回 UTC, 时差应等于 0
        re_utc = parsed_back.replace(tzinfo=dt.datetime.now().astimezone().tzinfo) \
            .astimezone(dt.timezone.utc)
        self.assertEqual(re_utc.hour, 13)
        # 空字符串/非法输入返回 None
        self.assertIsNone(_iso_to_local_str(''))
        self.assertIsNone(_iso_to_local_str('garbage'))


if __name__ == '__main__':
    unittest.main()
