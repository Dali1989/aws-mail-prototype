import io
import json
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_handler as setup
import csv_delivery


def error(code):
    e=setup.ClientError();e.response={'Error':{'Code':code}};return e


class MemoryS3:
    def __init__(self):
        self.objects={}
        self.get_keys=[]
        self.lock=threading.Lock()
        self.fail_result=False
    def get_object(self, Bucket, Key):
        with self.lock:
            self.get_keys.append(Key)
            if Key not in self.objects:raise error('NoSuchKey')
            return {'Body':io.BytesIO(self.objects[Key]),'ETag':'source-version'}
    def put_object(self, Bucket, Key, Body, **kwargs):
        with self.lock:
            if self.fail_result and '/result-' in Key:raise error('AccessDenied')
            if kwargs.get('IfNoneMatch')=='*' and Key in self.objects:raise error('PreconditionFailed')
            self.objects[Key]=Body
            return {'ETag':'saved'}


class CsvTests(unittest.TestCase):
    def setUp(self):
        os.environ.update(CSV_BUCKET='test-bucket',MAX_BATCH_SIZE='5')
        self.s3=MemoryS3()
        self.patch=patch('csv_delivery.storage',return_value=self.s3);self.patch.start();self.addCleanup(self.patch.stop)
        self.sent=[]
    def send(self,*args):self.sent.append(args);return 200,{'messageId':'test-id','message':'accepted'}
    def run_route(self,path,data,send=None):
        return csv_delivery.handle_csv(path,data,'sender@example.com',['test@example.com'],setup.handler.prepare_message,send or self.send)
    def preview(self):
        status,job=self.run_route('/csv/preview',{'subject':'Subject','body':'{{companyName}}\n{{contactName}}様','rows':[{'companyName':'Test Co','contactName':'Sato','email':'test@example.com'}]})
        self.assertEqual(status,200);return job
    def test_preview_no_send_and_no_source_file_read(self):
        job=self.preview();self.assertEqual(job['rows'][0]['body'],'Test Co\nSato様')
        self.assertFalse(self.sent);self.assertEqual(self.s3.get_keys,[])
        self.assertTrue(all(key.startswith('jobs/') for key in self.s3.objects))
    def test_snapshot_survives_changed_csv_and_request(self):
        job=self.preview();self.s3.objects['input/recipients.csv']=b'changed'
        status,result=self.run_route('/csv/send',{'jobId':job['jobId'],'index':0,'to':'other@example.com','body':'tampered'})
        self.assertEqual(status,200);self.assertEqual(self.sent[0][1:3],('test@example.com','Subject'));self.assertIn('Sato',self.sent[0][3])
    def test_duplicate_request_only_one_attempt(self):
        job=self.preview();data={'jobId':job['jobId'],'index':0}
        self.run_route('/csv/send',data);self.run_route('/csv/send',data)
        self.assertEqual(len(self.sent),1)
    def test_concurrent_claim_only_one_attempt(self):
        job=self.preview();data={'jobId':job['jobId'],'index':0}
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:self.run_route('/csv/send',data),range(2)))
        self.assertEqual(len(self.sent),1)
    def test_uncertain_send_not_retried(self):
        job=self.preview();data={'jobId':job['jobId'],'index':0}
        def uncertain(*args):self.sent.append(args);return 502,{'message':'unknown'}
        self.run_route('/csv/send',data,uncertain);self.assertEqual(self.run_route('/csv/send',data)[0],409);self.assertEqual(len(self.sent),1)
    def test_save_result_failure_keeps_claim(self):
        job=self.preview();self.s3.fail_result=True;data={'jobId':job['jobId'],'index':0}
        self.assertEqual(self.run_route('/csv/send',data)[0],502)
        self.assertEqual(self.run_route('/csv/send',data)[0],409);self.assertEqual(len(self.sent),1)
    def test_status_pending_accepted(self):
        job=self.preview();data={'jobId':job['jobId']}
        self.assertEqual(self.run_route('/csv/status',data)[1]['rows'][0]['state'],'pending')
        self.run_route('/csv/send',dict(data,index=0))
        self.assertEqual(self.run_route('/csv/status',data)[1]['rows'][0]['state'],'accepted')
    def test_expiration_and_invalid_id(self):
        self.assertEqual(self.run_route('/csv/send',{'jobId':'../../x','index':0})[0],400)
        job=self.preview();key=f"jobs/{job['jobId']}/preview.json";job['createdAt']=int(time.time())-90000;self.s3.objects[key]=json.dumps(job).encode()
        self.assertEqual(self.run_route('/csv/send',{'jobId':job['jobId'],'index':0})[0],400);self.assertFalse(self.sent)
    def test_invalid_browser_rows(self):
        good={'companyName':'A','contactName':'B','email':'test@example.com'}
        invalid=[None,{},[],[None],[dict(good,extra='x')],[dict(good,companyName='')],
                 [dict(good,contactName=3)],[dict(good,contactName='a\nb')],
                 [dict(good,email='other@example.com')],[good,good]]
        for rows in invalid:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):csv_delivery.validate_rows(rows,['test@example.com'],5)
    def test_csv_row_limit(self):
        rows=[{'companyName':'A','contactName':'B','email':f't{i}@example.com'} for i in range(6)]
        with self.assertRaises(ValueError):csv_delivery.validate_rows(rows,[f't{i}@example.com' for i in range(6)],5)
    def test_missing_browser_rows(self):
        self.assertEqual(self.run_route('/csv/preview',{})[0],400)
        self.assertEqual(self.s3.objects,{})
    def test_http_preview_auth_and_static_parser(self):
        e=setup.ContractTests().event({'rows':[{'companyName':'A','contactName':'B','email':'test@example.com'}],'subject':'x','body':'{{companyName}}'})
        e['rawPath']='/csv/preview'
        os.environ.update(ACCESS_TOKEN='test-token',SENDER_EMAIL='sender@example.com',ALLOWED_RECIPIENTS='test@example.com')
        self.assertEqual(setup.handler.lambda_handler(e,None)['statusCode'],200)
        e['headers']={}
        self.assertEqual(setup.handler.lambda_handler(e,None)['statusCode'],401)
        e['rawPath']='/csv-parser.js';e['requestContext']['http']['method']='GET'
        self.assertEqual(setup.handler.lambda_handler(e,None)['statusCode'],200)


if __name__=='__main__':unittest.main()
