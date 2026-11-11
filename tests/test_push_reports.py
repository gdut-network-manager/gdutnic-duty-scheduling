"""Complete-week digests, preview shape, delivery scheduling and authorization."""
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient
from test_permissions import PermissionFixture
from server import push_reports as push, db, permissions, member_store, admin_store, schedule_store, duty_settings, mailer


class TestPushReports(PermissionFixture):
    def config(self, **changes):
        config = deepcopy(push.DEFAULTS)
        config.update(changes)
        return config

    def enable(self, **changes):
        admin_store.save_smtp({'enabled':True,'host':'smtp.example.com','from_address':'bot@example.com'})
        return push.save(self.config(enabled=True,recipients=['one@example.com','two@example.com'],**changes),
                         now=datetime(2026,11,6,12,tzinfo=push.TZ))

    def event(self, day, name, status='late', checked=None, start='09:00'):
        sid = schedule_store.save({'day':day,'start':start,'end':'12:00','members':[name], 'location':''})
        with db.write() as conn:
            conn.execute('UPDATE attendance SET checked_at=?,status_override=? WHERE shift_id=?', (checked,status,sid))
        return sid

    def test_period_boundaries_and_cross_year(self):
        for weeks, start in [(1,'2026-11-01'),(2,'2026-10-25'),(4,'2026-10-11')]:
            first,end,cutoff = push.period(weeks,now=datetime(2026,11,8,0,tzinfo=push.TZ))
            self.assertEqual(str(first), start)
            self.assertEqual(str(end),'2026-11-07')
            self.assertEqual(cutoff.strftime('%H:%M:%S'),'23:59:59')
        self.assertEqual(str(push.period(1,now=datetime(2026,11,7,23,59,tzinfo=push.TZ))[1]),'2026-10-31')
        self.assertEqual(str(push.period(2,end='2027-01-02')[0]),'2026-12-20')
        with self.assertRaises(ValueError): push.period(1,end='2026-11-08')

    def test_validation_and_disabled_tail(self):
        config = self.config(recipients=[' One@example.com ','one@example.com'])
        result = push.validate(config)
        self.assertEqual(result['recipients'],['One@example.com'])
        config['sections']['late']['columns'][0]['enabled'] = False
        self.assertEqual(push.validate(config)['sections']['late']['columns'][-3]['key'],'name')
        for change in [{'weeks':3},{'weeks':True},{'send_time':'24:00'},{'title':'a\nb'},
                       {'recipients':['x@example.com\r\nBcc: evil@example.com']},{'recipients':['a b@c.com']},
                       {'sections':[]}, {'recipients':'x@example.com'}]:
            with self.subTest(change=change), self.assertRaises(ValueError): push.validate(self.config(**change))
        config['sections']['late']['columns'] = [{'key':'name','enabled':True}]*5
        with self.assertRaises(ValueError): push.validate(config)
        with self.assertRaises(ValueError): push.save(self.config(enabled=True))

    def test_next_sunday_preserved_and_time_change_rearms(self):
        saved = self.enable(weeks=2)
        self.assertEqual(saved['next_run'],'2026-11-08T09:00:00+08:00')
        saved['opening']='New'
        self.assertEqual(push.save(saved,now=datetime(2026,11,9,tzinfo=push.TZ))['next_run'],saved['next_run'])
        saved['send_time']='10:30'
        self.assertEqual(push.save(saved,now=datetime(2026,11,9,tzinfo=push.TZ))['next_run'],'2026-11-15T10:30:00+08:00')
        self.assertEqual(push.next_sunday(datetime(2026,11,8,9,tzinfo=push.TZ),'09:00').day,15)
        saved['enabled']=False
        self.assertIsNone(push.save(saved)['next_run'])

    def test_real_events_month_total_groups_and_date_continuations(self):
        permissions.ensure_defaults(); member_store.create('张三')
        group = permissions.save_group('值班组',['duty']); permissions.set_group('张三',group['id'])
        self.event('2026-11-01','张三'); self.event('2026-11-08','张三'); self.event('2026-11-10','张三')
        self.event('2026-11-09','李四','absent')
        config=self.config()
        for section in config['sections'].values():
            for col in section['columns']: col['enabled']=True
        result=push.report(config,end='2026-11-14')
        rows=result['sections']['late']['rows']
        self.assertEqual(rows,[['张三',2,'2026-11-08 09:00',3,'值班组'],['','','2026-11-10 09:00','','']])
        self.assertEqual(result['sections']['absent']['rows'][0][:2],['李四',1])
        config['sections']['late']['columns'][2]['enabled']=False
        self.assertEqual(len(push.report(config,end='2026-11-14')['sections']['late']['rows']),1)

    def test_classification_cutoff_overrides_and_archive_dedup(self):
        duty_settings.save({'late_minutes':5,'absent_minutes':30,'early_minutes':10})
        self.event('2026-11-02','晚到','', '2026-11-02T09:06:00+08:00')
        self.event('2026-11-03','缺席','')
        self.event('2026-11-04','覆盖','normal')
        self.event('2026-11-07','跨界签到','', '2026-11-08T00:01:00+08:00',start='23:50')
        with db.write() as conn:
            for day,name,at in [('2026-11-02','晚到','2026-11-02T09:06:00+08:00'),('2026-11-05','归档','2026-11-05T09:06:00+08:00')]:
                conn.execute('INSERT INTO attendance_history(day,start,end,name,checked_at,note,archived_at) VALUES(?,?,?,?,?,?,?)',
                             (day,'09:00','12:00',name,at,'','2026-11-06'))
        result=push.report(self.config(),end='2026-11-07')
        self.assertEqual(result['sections']['late']['people'],2)
        self.assertEqual(result['sections']['absent']['people'],1) # 23:50 is still pending at cutoff

    def test_email_escaping_and_sample_are_preview_only(self):
        result=push.report(self.config(title='<title>',opening='<script>x</script>', closing='A&B'),end='2026-11-07',sample=True)
        plain,html=push.render_email(result)
        self.assertIn('示例成员',plain); self.assertIn('&lt;script&gt;',html); self.assertNotIn('<script>',html)
        self.assertIn('A&amp;B',html)
        result['sections']['late']['enabled']=False
        self.assertNotIn('<h2 style="font-size:18px;color:#186078">迟到',push.render_email(result)[1])

    def test_due_time_no_duplicates_and_next_interval(self):
        self.enable(weeks=2)
        with patch.object(mailer,'send') as send:
            push.run_due(datetime(2026,11,8,8,59,tzinfo=push.TZ)); send.assert_not_called()
            push.run_due(datetime(2026,11,8,9,tzinfo=push.TZ))
            self.assertEqual(send.call_count,2)
            self.assertIn('2026-10-25',send.call_args.args[1]); self.assertIn('2026-11-07',send.call_args.args[1])
            self.assertNotIn('示例成员',send.call_args.args[2]); self.assertIn('html',send.call_args.kwargs)
            push.run_due(datetime(2026,11,8,9,1,tzinfo=push.TZ)); self.assertEqual(send.call_count,2)
        self.assertEqual(push.load()['next_run'],'2026-11-22T09:00:00+08:00')

    def test_failure_retries_only_unsent_recipient_and_respects_disable(self):
        self.enable()
        def send(address,*args,**kwargs):
            if address.startswith('two'): raise OSError('offline')
        at=datetime(2026,11,8,9,tzinfo=push.TZ)
        with patch.object(mailer,'send',side_effect=send) as mock:
            push.run_due(at); self.assertEqual(mock.call_count,2)
            push.run_due(at+timedelta(minutes=14)); self.assertEqual(mock.call_count,2)
        with patch.object(mailer,'send') as mock:
            push.run_due(at+timedelta(minutes=15)); mock.assert_called_once()
            self.assertEqual(mock.call_args.args[0],'two@example.com')
        self.assertEqual(push.load()['next_run'],'2026-11-15T09:00:00+08:00')
        config=push.load(); config['enabled']=False; push.save(config)
        with patch.object(mailer,'send') as mock:
            push.run_due(at+timedelta(weeks=2)); mock.assert_not_called()

    def test_restart_downtime_uses_latest_due_complete_period(self):
        self.enable(weeks=2)
        with patch.object(mailer,'send') as mock:
            push.run_due(datetime(2026,12,7,10,tzinfo=push.TZ))
            self.assertIn('2026-12-05',mock.call_args.args[1])
        self.assertEqual(push.load()['next_run'],'2026-12-20T09:00:00+08:00')

    def test_concurrent_workers_claim_each_recipient_once(self):
        import threading
        self.enable()
        at=datetime(2026,11,8,9,tzinfo=push.TZ)
        started=threading.Event(); release=threading.Event(); failures=[]
        def send(address,*args,**kwargs):
            if address.startswith('one'):
                started.set()
                if not release.wait(3): raise RuntimeError('test timed out')
        def worker():
            try: push.run_due(at)
            except Exception as exc: failures.append(exc)
        with patch.object(mailer,'send',side_effect=send) as mock:
            thread=threading.Thread(target=worker); thread.start()
            try:
                self.assertTrue(started.wait(3))
                push.run_due(at)
            finally:
                release.set(); thread.join(3)
            self.assertFalse(thread.is_alive()); self.assertEqual(failures,[])
            self.assertCountEqual([call.args[0] for call in mock.call_args_list],['one@example.com','two@example.com'])

    def test_disable_during_delivery_stops_remaining_recipients(self):
        self.enable()
        def send(*args,**kwargs):
            config=push.load(); config['enabled']=False; push.save(config)
        with patch.object(mailer,'send',side_effect=send) as mock:
            push.run_due(datetime(2026,11,8,9,tzinfo=push.TZ))
            mock.assert_called_once()
        self.assertFalse(push.load()['enabled'])
        self.assertIsNone(push.load()['next_run'])

    def test_mailer_includes_plaintext_and_html_alternatives(self):
        from unittest.mock import Mock
        self.enable()
        client=Mock()
        with patch.object(mailer,'_connect',return_value=client):
            mailer.send('one@example.com','考勤','纯文本',html='<p>表格</p>')
        message=client.send_message.call_args.args[0]
        self.assertEqual(message.get_content_type(),'multipart/alternative')
        self.assertIn('纯文本',message.get_body(preferencelist=('plain',)).get_content())
        self.assertIn('<p>表格</p>',message.get_body(preferencelist=('html',)).get_content())

    def test_api_has_own_permission_and_rejects_malformed_preview(self):
        member_store.create('operator','password123'); permissions.set_permissions('operator',['push'])
        admin_store.remember_setup('operator')
        client=TestClient(__import__('server.app',fromlist=['app']).app)
        client.get('/'+admin_store.path())
        self.assertEqual(client.post('/api/admin/login',json={'username':'operator','password':'password123'}).status_code,200)
        self.assertEqual(client.get('/api/admin/push').status_code,200)
        self.assertEqual(client.get('/api/admin/smtp').status_code,403)
        self.assertEqual(client.post('/api/admin/push/preview',json=[]).status_code,400)
        self.assertEqual(client.post('/api/admin/push/preview',json={'config':self.config(),'sample':True}).status_code,200)
        self.assertEqual(client.put('/api/admin/push',json=self.config()).status_code,200)
        permissions.set_permissions('operator',['members'])
        for method,path in [('get','/api/admin/push'),('put','/api/admin/push'),('post','/api/admin/push/preview')]:
            self.assertEqual(getattr(client,method)(path).status_code,403)
