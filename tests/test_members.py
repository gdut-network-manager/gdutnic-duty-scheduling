import unittest
import test_schedule
from server import admin_store, db, member_store


class MemberTest(unittest.TestCase):
    setUp = test_schedule.ScheduleTest.setUp
    tearDown = test_schedule.ScheduleTest.tearDown

    def test_provision_hashes_and_preserves_existing_password(self):
        self.assertEqual(member_store.create_missing(['张三','张三','李四'])['created'], 2)
        self.assertTrue(member_store.verify('张三', 'gg2017'))
        with db.read() as conn:
            self.assertNotEqual(conn.execute('SELECT password FROM member_accounts WHERE username=?', ('张三',)).fetchone()['password'], 'gg2017')
        member_store.change_password('张三','gg2017','my-new-password')
        member_store.create_missing(['张三'])
        self.assertFalse(member_store.verify('张三','gg2017'))
        self.assertTrue(member_store.verify('张三','my-new-password'))
        self.assertFalse(admin_store.verify_login('张三','my-new-password'))

    def test_disable_reset_and_session_invalidation(self):
        member_store.create_missing(['张三'])
        version=member_store.session_version('张三')
        self.assertTrue(member_store.session_valid('张三',version))
        member_store.update('张三',active=False)
        self.assertFalse(member_store.verify('张三','gg2017'))
        self.assertFalse(member_store.session_valid('张三',version))
        member_store.update('张三',active=True,reset=True)
        self.assertFalse(member_store.session_valid('张三',version))
        self.assertTrue(member_store.verify('张三','gg2017'))

    def test_add_and_delete_member_accounts(self):
        member_store.create('王五')
        self.assertTrue(member_store.verify('王五', 'gg2017'))
        with self.assertRaises(ValueError):
            member_store.create('王五')
        member_store.delete('王五')
        self.assertFalse(member_store.verify('王五', 'gg2017'))
        self.assertEqual(member_store.list_accounts(), [])
        with self.assertRaises(LookupError):
            member_store.delete('王五')

    def test_admin_identity_cache_tracks_writes(self):
        admin_store.register('admin', 'password123')
        self.assertEqual(admin_store.usernames(), ['admin'])
        self.assertEqual(admin_store.usernames(), ['admin'])  # served from cache
        admin_store.add_user('second', 'password456')
        self.assertEqual(admin_store.usernames(), ['admin', 'second'])
        admin_store.update_user('second', 'renamed', '')
        self.assertEqual(admin_store.usernames(), ['admin', 'renamed'])
        admin_store.delete_user('renamed', 'admin')
        self.assertEqual(admin_store.usernames(), ['admin'])

    def test_admin_identity_cache_survives_another_writer(self):
        admin_store.register('admin', 'password123')
        admin_store.path()
        # A second process writes directly; the cached identity must be refreshed on its next write.
        with db.write() as conn:
            conn.execute("INSERT INTO users(username,password) VALUES(?,?)", ('direct', 'hash'))
            for row in conn.execute("SELECT username FROM users"):
                pass
        admin_store.add_user('later', 'password456')
        self.assertEqual(admin_store.usernames(), ['admin', 'direct', 'later'])

    def test_atomic_admin_account_management(self):
        admin_store.register('admin','password123')
        with self.assertRaises(ValueError): admin_store.register('second','password123')
        admin_store.add_user('second','password456')
        admin_store.update_user('second','renamed','new-password')
        self.assertFalse(admin_store.verify_login('second','password456'))
        self.assertTrue(admin_store.verify_login('renamed','new-password'))
        admin_store.delete_user('renamed','admin')
        self.assertEqual(admin_store.usernames(), ['admin'])
        with self.assertRaises(ValueError): admin_store.delete_user('admin')
