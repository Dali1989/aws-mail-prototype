"""Offline contract checks. Never contact AWS or send mail."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
client = Mock()
client.send_email.return_value = {"MessageId": "offline-test-id"}
sys.modules['boto3'] = types.SimpleNamespace(client=Mock(return_value=client))
sys.modules['botocore'] = types.ModuleType('botocore')
sys.modules['botocore.config'] = types.SimpleNamespace(Config=lambda **kw: kw)
class ClientError(Exception):
    def __init__(self): self.response = {"Error": {"Code": "AccessDenied"}}
class BotoCoreError(Exception): pass
sys.modules['botocore.exceptions'] = types.SimpleNamespace(ClientError=ClientError, BotoCoreError=BotoCoreError)
spec=importlib.util.spec_from_file_location('handler', ROOT/'src/lambda_function.py')
handler=importlib.util.module_from_spec(spec)
spec.loader.exec_module(handler)

class ContractTests(unittest.TestCase):
    def setUp(self):
        client.reset_mock()
        client.send_email.side_effect=None
        os.environ.update(ACCESS_TOKEN='test-token', SENDER_EMAIL='sender@example.com', ALLOWED_RECIPIENTS='test@example.com')
    def event(self, data=None, method='POST', path='/send'):
        return {'rawPath':path,'requestContext':{'http':{'method':method}},'headers':{'Authorization':'Bearer test-token'},'body':json.dumps(data if data is not None else {'to':'test@example.com','subject':'件名','body':'本文'})}
    def test_page_no_secret(self):
        r=handler.lambda_handler(self.event(method='GET',path='/'),None)
        self.assertEqual(r['statusCode'],200)
        self.assertNotIn('test-token',r['body'])
    def test_missing_key_does_not_send(self):
        e=self.event();e['headers']={}
        self.assertEqual(handler.lambda_handler(e,None)['statusCode'],401)
        client.send_email.assert_not_called()
    def test_allowlist_rejects_other_recipient(self):
        self.assertEqual(handler.lambda_handler(self.event({'to':'other@example.com','subject':'x','body':'x'}),None)['statusCode'],400)
        client.send_email.assert_not_called()
    def test_sender_cannot_be_forged(self):
        self.assertEqual(handler.lambda_handler(self.event({'to':'test@example.com','sender':'other@example.com','subject':'x','body':'x'}),None)['statusCode'],400)
        client.send_email.assert_not_called()
    def test_blank_subject_and_bad_json(self):
        e=self.event({'to':'test@example.com','subject':'  ','body':'x'})
        self.assertEqual(handler.lambda_handler(e,None)['statusCode'],400)
        e['body']='[]'
        self.assertEqual(handler.lambda_handler(e,None)['statusCode'],400)
    def test_config_auth(self):
        r=handler.lambda_handler(self.event(method='GET',path='/api/config'),None)
        self.assertEqual(json.loads(r['body'])['sender'],'sender@example.com')
    def test_send_is_one_recipient(self):
        r=handler.lambda_handler(self.event(),None)
        self.assertEqual(r['statusCode'],200)
        self.assertEqual(client.send_email.call_count,1)
        self.assertEqual(client.send_email.call_args.kwargs['Destination'],{'ToAddresses':['test@example.com']})
    def test_uncertain_error_no_auto_retry(self):
        client.send_email.side_effect=BotoCoreError()
        self.assertEqual(handler.lambda_handler(self.event(),None)['statusCode'],502)
        self.assertEqual(client.send_email.call_count,1)
    def test_standard_template_personalization(self):
        template=(ROOT/'src/default-body.txt').read_text(encoding='utf-8').rstrip('\n')
        e=self.event({'to':'test@example.com','subject':'件名','body':template,'companyName':'富士山ソフトウエア株式会社','contactName':'佐藤'})
        self.assertEqual(handler.lambda_handler(e,None)['statusCode'],200)
        actual=client.send_email.call_args.kwargs['Message']['Body']['Text']['Data']
        self.assertEqual(actual,template.replace('{{companyName}}','富士山ソフトウエア株式会社').replace('{{contactName}}','佐藤'))
        self.assertTrue(actual.startswith('富士山ソフトウエア株式会社\n佐藤様\n'))
    def test_unknown_or_missing_variable_cannot_send(self):
        for text in ['{{other}}','{{companyName}}']:
            self.assertEqual(handler.lambda_handler(self.event({'to':'test@example.com','subject':'x','body':text}),None)['statusCode'],400)
        client.send_email.assert_not_called()
    def test_customer_text_is_not_reinterpreted(self):
        e=self.event({'to':'test@example.com','subject':'x','body':'{{companyName}}\n{{contactName}}様','companyName':'株式会社{{contactName}}','contactName':'佐藤'})
        self.assertEqual(handler.lambda_handler(e,None)['statusCode'],200)
        actual=client.send_email.call_args.kwargs['Message']['Body']['Text']['Data']
        self.assertEqual(actual,'株式会社{{contactName}}\n佐藤様')

if __name__=='__main__':unittest.main()
