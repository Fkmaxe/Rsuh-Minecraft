import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from orchestrator.core import Core
from orchestrator.server import serve
from test_core import FakeDeployer


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.core=Core(Path(self.temp.name)/'db',FakeDeployer())
        self.server=serve(self.core,{'host':'127.0.0.1','api_port':0},'test-secret')
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.core.db.close();self.temp.cleanup()
    def request(self,path,body=None,token='test-secret'):
        headers={'Authorization':'Bearer '+token}
        req=urllib.request.Request(self.base+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req,timeout=5) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:
            with e:return e.code,json.load(e)
    def test_authentication_on_reads_and_writes(self):
        self.assertEqual(401,self.request('/health',token='wrong')[0])
        self.assertEqual(401,self.request('/deploy-test',{},token='')[0])
        self.assertFalse(self.core.active)
        self.assertEqual(200,self.request('/health')[0])
    def test_validation_and_method(self):
        self.assertEqual(400,self.request('/command',{'player':'bad','name':'Player','action':'queue'})[0])
        self.assertEqual(400,self.request('/command',[])[0])
        self.assertEqual(404,self.request('/deploy-test')[0])
        self.assertEqual(404,self.request('/missing',{})[0])
    def test_real_deployment_api_and_abort(self):
        status,result=self.request('/deploy-test',{'mode':'5v5'})
        self.assertEqual(200,status);mid=result['match']
        self.assertEqual([5,5],list(map(len,self.core.active[mid]['teams'])))
        self.assertFalse(self.core.active[mid]['ranked'])
        self.assertEqual(200,self.request('/abort',{'match':mid})[0]);self.assertFalse(self.core.active)


if __name__=='__main__':unittest.main()
