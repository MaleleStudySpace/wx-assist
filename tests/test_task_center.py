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

    def test_complete_task_truncates_result(self):
        tid = self.tc.create_task('group_digest', 'manual', 'g1', '群1')
        long_result = 'x' * 1000
        self.tc.complete_task(tid, result=long_result)
        task = self.tc.get_task(tid)
        self.assertEqual(len(task['result']), 500)  # truncated to 500


if __name__ == '__main__':
    unittest.main()
