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

    def test_list_tasks_exclude(self):
        self.tc.create_task('cache_oa_incremental', 'system', 'g1', 'OA增量同步')
        self.tc.create_task('cache_fav_incremental', 'system', 'g2', '收藏增量')
        self.tc.create_task('group_digest', 'manual', 'g3', '群3')
        tasks = self.tc.list_tasks(exclude='cache_')
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['task_type'], 'group_digest')


if __name__ == '__main__':
    unittest.main()
