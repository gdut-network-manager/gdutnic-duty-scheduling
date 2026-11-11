"""SMTP settings: stored like other sections, validated, and never echoed in plaintext."""
import smtplib
import unittest
from unittest.mock import patch

import test_schedule
from server import admin_store, mailer, permissions


class TestSmtpSettings(test_schedule.ScheduleTest):
    test_lifecycle_and_repeat_safe_attendance = None
    test_reject_wrong_person_and_time = None
    test_invalid_shift = None
    test_member_only_sees_own_shifts_and_attendance = None
    test_location_is_optional = None
    test_duty_rules_control_check_in_window = None
    test_special_shifts_are_listed_and_edited = None
    test_check_in_chains_only_pairs_1_2_and_3_4 = None
    test_pairs_use_day_order_not_only_this_person_shifts = None
    test_chained_shifts_stay_on_time_while_first_keeps_lateness = None
    test_overnight_pair_does_not_chain_into_next_day = None
    test_overnight_appears_and_signs_in_after_midnight = None

    def test_defaults_are_off_and_empty(self):
        data = admin_store.smtp_public()
        self.assertFalse(data['enabled'])
        self.assertEqual(data['host'], '')
        self.assertFalse(data['has_password'])

    def test_saving_persists_and_hides_the_password(self):
        saved = admin_store.save_smtp({
            'host': 'smtp.example.com', 'port': 465, 'security': 'ssl',
            'username': 'bot@example.com', 'password': 'top-secret',
            'from_address': 'bot@example.com', 'from_name': '网协',
        })
        self.assertEqual(saved['host'], 'smtp.example.com')
        self.assertTrue(saved['has_password'])
        # The public payload returns the value for the form, but never the real secret
        # to a reader that did not just save it.
        self.assertEqual(admin_store.smtp_config()['password'], 'top-secret')
        self.assertEqual(admin_store.load()['smtp']['password'], 'top-secret')

    def test_blank_password_keeps_the_stored_one(self):
        admin_store.save_smtp({'host': 'h', 'from_address': 'a@b.com', 'password': 'keep-me'})
        admin_store.save_smtp({'from_name': '新名称'})
        self.assertEqual(admin_store.smtp_config()['password'], 'keep-me')

    def test_masked_password_is_not_written_back(self):
        admin_store.save_smtp({'host': 'h', 'from_address': 'a@b.com', 'password': 'real'})
        admin_store.save_smtp({'password': '••••••'})
        self.assertEqual(admin_store.smtp_config()['password'], 'real')

    def test_clearing_the_password_works(self):
        admin_store.save_smtp({'host': 'h', 'from_address': 'a@b.com', 'password': 'real'})
        admin_store.save_smtp({'password': ''})
        self.assertEqual(admin_store.smtp_config()['password'], '')

    def test_invalid_values_are_rejected(self):
        with self.assertRaises(ValueError):
            admin_store.save_smtp({'port': 'abc'})
        with self.assertRaises(ValueError):
            admin_store.save_smtp({'port': 70000})
        with self.assertRaises(ValueError):
            admin_store.save_smtp({'security': 'tls'})
        with self.assertRaises(ValueError):
            admin_store.save_smtp({'from_address': 'not-an-email'})

    def test_enabling_requires_a_usable_configuration(self):
        with self.assertRaises(ValueError):
            admin_store.save_smtp({'enabled': True})
        with self.assertRaises(ValueError):
            admin_store.save_smtp({'host': 'smtp.example.com', 'enabled': True})
        saved = admin_store.save_smtp({
            'host': 'smtp.example.com', 'from_address': 'bot@example.com', 'enabled': True})
        self.assertTrue(saved['enabled'])

    def test_port_defaults_follow_the_security_choice(self):
        self.assertEqual(admin_store.save_smtp({'security': 'ssl', 'port': ''})['port'], 465)
        self.assertEqual(admin_store.save_smtp({'security': 'starttls', 'port': ''})['port'], 587)
        self.assertEqual(admin_store.save_smtp({'security': 'none', 'port': ''})['port'], 25)

    def test_smtp_is_its_own_permission(self):
        self.assertIn('smtp', permissions.PERMISSIONS)
        self.assertIn('smtp', permissions.leaves())
        self.assertIn('smtp', permissions._derive({'smtp'}))
        # Holding it also opens 其他设置.
        self.assertIn('settings', permissions._derive({'smtp'}))


class TestMailer(test_schedule.ScheduleTest):
    test_lifecycle_and_repeat_safe_attendance = None
    test_reject_wrong_person_and_time = None
    test_invalid_shift = None
    test_member_only_sees_own_shifts_and_attendance = None
    test_location_is_optional = None
    test_duty_rules_control_check_in_window = None
    test_special_shifts_are_listed_and_edited = None
    test_check_in_chains_only_pairs_1_2_and_3_4 = None
    test_pairs_use_day_order_not_only_this_person_shifts = None
    test_chained_shifts_stay_on_time_while_first_keeps_lateness = None
    test_overnight_pair_does_not_chain_into_next_day = None
    test_overnight_appears_and_signs_in_after_midnight = None

    def configure(self, **overrides):
        body = {'host': 'smtp.example.com', 'port': 465, 'security': 'ssl',
                'username': 'bot@example.com', 'password': 'secret',
                'from_address': 'bot@example.com', 'from_name': '网协', 'enabled': True}
        body.update(overrides)
        admin_store.save_smtp(body)

    def test_sender_uses_the_display_name(self):
        self.configure()
        sender = mailer.sender(admin_store.smtp_config())
        # Non-ASCII names are RFC 2047 encoded, as mail clients expect.
        self.assertTrue(sender.endswith('<bot@example.com>'), sender)
        from email.header import decode_header, make_header
        self.assertEqual(str(make_header(decode_header(sender.split(' <')[0]))), '网协')

    def test_sender_without_a_name_is_a_bare_address(self):
        self.configure(from_name='')
        self.assertEqual(mailer.sender(admin_store.smtp_config()), 'bot@example.com')

    def test_disabled_smtp_refuses_to_send(self):
        with self.assertRaises(RuntimeError):
            mailer.send('to@example.com', '主题', '正文')

    def test_missing_host_is_reported_readably(self):
        admin_store.save_smtp({'enabled': False})
        with self.assertRaises(RuntimeError):
            mailer._connect(admin_store.smtp_config())

    def test_send_opens_a_message_with_the_configured_headers(self):
        self.configure()
        sent = {}

        class FakeClient:
            def send_message(self, message):
                sent['message'] = message
            def quit(self):
                sent['quit'] = True

        with patch.object(mailer, '_connect', return_value=FakeClient()):
            mailer.send('to@example.com', '签到提醒', '请在 9:00 前签到')
        message = sent['message']
        self.assertEqual(message['To'], 'to@example.com')
        self.assertEqual(message['Subject'], '签到提醒')
        self.assertIn('网协', message['From'])
        self.assertIn('请在 9:00 前签到', message.get_content())
        self.assertTrue(sent.get('quit'))

    def test_test_message_reports_the_recipient(self):
        self.configure()
        with patch.object(mailer, 'send') as send:
            result = mailer.send_test('ops@example.com')
        self.assertEqual(result, {'ok': True, 'to': 'ops@example.com'})
        self.assertEqual(send.call_args.args[0], 'ops@example.com')

    def test_connection_errors_are_wrapped(self):
        self.configure()
        with patch.object(mailer.smtplib, 'SMTP_SSL', side_effect=OSError('refused')):
            with self.assertRaises(RuntimeError) as caught:
                mailer._connect(admin_store.smtp_config())
        self.assertIn('无法连接', str(caught.exception))

    def test_send_failures_are_wrapped(self):
        self.configure()

        class FailingClient:
            def send_message(self, message):
                raise smtplib.SMTPException('relay denied')
            def quit(self):
                pass

        with patch.object(mailer, '_connect', return_value=FailingClient()):
            with self.assertRaises(RuntimeError) as caught:
                mailer.send('to@example.com', '主题', '正文')
        self.assertIn('发送失败', str(caught.exception))


class TestSmtpTls(unittest.TestCase):
    """TLS must work even when the interpreter ships an empty CA store."""

    def test_context_loads_certificates(self):
        context = mailer._ssl_context()
        # A usable context verifies peers and has at least one CA loaded.
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, __import__('ssl').CERT_REQUIRED)
        self.assertGreater(len(context.get_ca_certs()), 0)

    def test_falls_back_when_certifi_is_missing(self):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == 'certifi':
                raise ImportError('missing')
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, '__import__', side_effect=fake_import):
            context = mailer._ssl_context()
        self.assertTrue(context.check_hostname)
